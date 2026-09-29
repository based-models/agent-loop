"""The Responses codec: stateless requests, output items, replay from the origin.

Fixtures follow the documented shapes (OpenAI function-calling, reasoning and
web-search guides), not a live capture.
"""

import logging
from typing import Any

import httpx
import pytest

from agentloop.errors import (
    BadRequestError,
    ConfigurationError,
    ProviderError,
    RateLimited,
    ResponseInvalid,
)
from agentloop.provider import HostedTool, ModelRequest, StopReason, ToolSchema
from agentloop.providers._openai_compat import OpenAICompatClient, Quirks
from agentloop.providers._openai_responses import (
    message_to_items,
    request_to_wire,
    response_from_wire,
)
from agentloop.transcript import (
    HostedToolPart,
    Message,
    Origin,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)

DEFAULT = Quirks()
SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
READ = ToolSchema(name="read", description="Read a file.", parameters=SCHEMA)
SEARCH = HostedTool(kind="web_search")


def wire(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("messages", ())
    return request_to_wire(ModelRequest(**kwargs), model="gpt-x", quirks=DEFAULT, provider="openai")


def body(*output: dict[str, Any], status: str = "completed", **extra: Any) -> dict[str, Any]:
    return {"id": "resp_1", "model": "gpt-x", "status": status, "output": list(output), **extra}


def parse(b: dict[str, Any]) -> Any:
    return response_from_wire(b, provider="openai", quirks=DEFAULT)


MESSAGE = {
    "type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
    "phase": "final_answer",
    "content": [{
        "type": "output_text", "text": "Guidance changed on 3 March.",
        "annotations": [{"type": "url_citation", "url": "https://europa.eu/x",
                         "title": "EU", "start_index": 0, "end_index": 28}],
    }],
}
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "hm"}],
             "encrypted_content": "gAAAA-opaque"}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "read",
        "arguments": '{"path": "a.py"}', "status": "completed"}
SEARCH_CALL = {"type": "web_search_call", "id": "ws_1", "status": "completed",
               "action": {"type": "search", "query": "EU AI Act guidance",
                          "sources": [{"type": "url", "url": "https://europa.eu/x"}]}}


# --- the request: stateless, flat tools, hosted search ---


def test_every_request_is_stateless() -> None:
    payload = wire(system="be brief", max_tokens=100)
    assert payload["store"] is False                  # the API defaults to TRUE
    assert "previous_response_id" not in payload
    assert payload["instructions"] == "be brief"
    assert payload["max_output_tokens"] == 100


def test_function_tools_are_flat() -> None:
    [tool] = wire(tools=(READ,))["tools"]
    assert tool == {"type": "function", "name": "read", "description": "Read a file.",
                    "parameters": SCHEMA, "strict": True}


def test_user_text_and_tool_results_become_items() -> None:
    items = message_to_items(Message(role="user", content=(
        ToolResultPart(call_id="call_1", tool_name="read", content="ok"),
    )))
    assert items == [{"type": "function_call_output", "call_id": "call_1", "output": "ok"}]
    items = message_to_items(Message(role="user", content=(TextPart(text="hi"),)))
    assert items == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]


def test_hosted_search_translates_and_asks_for_sources() -> None:
    tool = HostedTool(kind="web_search", options={
        "allowed_domains": ["europa.eu"], "search_context_size": "high",
        "user_location": {"country": "BE"},
    })
    payload = wire(hosted_tools=(tool,))
    assert payload["tools"] == [{
        "type": "web_search", "filters": {"allowed_domains": ["europa.eu"]},
        "search_context_size": "high",
        "user_location": {"type": "approximate", "country": "BE"},
    }]
    assert payload["include"] == ["web_search_call.action.sources"]


def test_anthropic_only_tuning_is_logged_not_sent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        payload = wire(hosted_tools=(HostedTool(kind="web_search", options={"max_uses": 3}),))
    assert payload["tools"] == [{"type": "web_search"}]
    assert "max_uses" in caplog.text


def test_an_unknown_option_is_refused_not_dropped() -> None:
    with pytest.raises(ConfigurationError, match="unknown web_search option"):
        wire(hosted_tools=(HostedTool(kind="web_search", options={"safe_mode": True}),))


def test_allowed_and_blocked_domains_are_exclusive() -> None:
    tool = HostedTool(kind="web_search", options={"allowed_domains": ["a.com"],
                                                  "blocked_domains": ["b.com"]})
    with pytest.raises(ConfigurationError, match="not both"):
        wire(hosted_tools=(tool,))


def test_stop_sequences_are_logged_because_responses_has_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        payload = wire(stop_sequences=("END",))
    assert "stop" not in payload and "stop_sequences" in caplog.text


# --- the response: output items, in order ---


def test_text_keeps_its_citations_as_spans() -> None:
    response = parse(body(MESSAGE))
    [text] = response.content
    assert isinstance(text, TextPart)
    [citation] = text.citations
    assert (citation.url, citation.start, citation.end) == ("https://europa.eu/x", 0, 28)
    assert response.stop_reason is StopReason.END_TURN


def test_reasoning_and_a_call_become_thinking_and_tool_use() -> None:
    response = parse(body(REASONING, CALL))
    thinking, call = response.content
    assert isinstance(thinking, ThinkingPart) and thinking.text == "hm"
    assert isinstance(call, ToolCallPart)
    assert call.id == "call_1"                        # call_id, not the item id
    assert call.arguments == {"path": "a.py"}
    assert response.stop_reason is StopReason.TOOL_USE


def test_a_search_becomes_a_hosted_part_with_its_sources() -> None:
    response = parse(body(SEARCH_CALL, MESSAGE))
    search = response.content[0]
    assert isinstance(search, HostedToolPart)
    assert (search.kind, search.query) == ("web_search", "EU AI Act guidance")
    assert [s.url for s in search.sources] == ["https://europa.eu/x"]


def test_the_origin_keeps_every_item_verbatim() -> None:
    response = parse(body(REASONING, SEARCH_CALL, MESSAGE))
    assert response.origin is not None
    assert (response.origin.provider, response.origin.api) == ("openai", "responses")
    assert list(response.origin.raw) == [REASONING, SEARCH_CALL, MESSAGE]


def test_a_call_cut_off_by_the_token_limit_stays_max_tokens() -> None:
    response = parse(body(CALL, status="incomplete",
                          incomplete_details={"reason": "max_output_tokens"}))
    assert response.stop_reason is StopReason.MAX_TOKENS
    assert response.raw_stop_reason == "incomplete:max_output_tokens"


def test_a_content_filter_stop_is_a_refusal() -> None:
    response = parse(body(MESSAGE, status="incomplete",
                          incomplete_details={"reason": "content_filter"}))
    assert response.stop_reason is StopReason.REFUSAL


def test_a_refusal_item_is_a_refusal() -> None:
    refusal = {"type": "message", "role": "assistant",
               "content": [{"type": "refusal", "refusal": "I can't help with that."}]}
    response = parse(body(refusal))
    assert response.stop_reason is StopReason.REFUSAL
    assert response.content[0] == TextPart(text="I can't help with that.")


def test_a_failed_response_raises_the_mapped_error() -> None:
    with pytest.raises(RateLimited):
        parse(body(status="failed", error={"code": "rate_limit_exceeded", "message": "slow"}))
    with pytest.raises(ProviderError, match="bad prompt"):
        parse(body(status="failed", error={"code": "invalid_prompt", "message": "bad prompt"}))


def test_an_unfinished_or_malformed_response_is_invalid() -> None:
    with pytest.raises(ResponseInvalid, match="in_progress"):
        parse(body(MESSAGE, status="in_progress"))
    with pytest.raises(ResponseInvalid):
        parse(body({"type": "message", "content": [{"type": "output_text"}]}))  # no text
    with pytest.raises(ResponseInvalid):
        parse(body())                                                            # empty


def test_usage_maps_cached_and_reasoning_tokens() -> None:
    usage = parse(body(MESSAGE, usage={
        "input_tokens": 10, "output_tokens": 5,
        "input_tokens_details": {"cached_tokens": 4},
        "output_tokens_details": {"reasoning_tokens": 3},
    })).usage
    assert (usage.input_tokens, usage.output_tokens) == (10, 5)
    assert (usage.cache_read_tokens, usage.reasoning_tokens) == (4, 3)


# --- replay: the origin, for this codec; the parts, for any other ---


def test_a_turn_from_this_codec_is_replayed_from_its_origin_without_ids() -> None:
    response = parse(body(REASONING, SEARCH_CALL, MESSAGE))
    message = Message(role="assistant", content=response.content, origin=response.origin)
    items = message_to_items(message, "openai")
    assert [item.get("type") for item in items] == ["reasoning", "message"]
    assert all("id" not in item for item in items)     # store=false: ids cannot resolve
    assert items[0]["encrypted_content"] == "gAAAA-opaque"
    assert items[1]["phase"] == "final_answer"          # a dropped phase changes behaviour


def test_a_turn_from_chat_completions_is_rebuilt_from_its_parts() -> None:
    message = Message(
        role="assistant",
        content=(ThinkingPart(text="x"), TextPart(text="Reading."),
                 ToolCallPart(id="call_9", tool_name="read", arguments={"path": "a"})),
        origin=Origin(provider="openai", api="completions", model="m", raw=({},)),
    )
    assert message_to_items(message, "openai") == [
        {"role": "assistant", "content": "Reading."},
        {"type": "function_call", "call_id": "call_9", "name": "read",
         "arguments": '{"path": "a"}'},
    ]


def test_another_providers_origin_is_never_replayed_here() -> None:
    message = Message(role="assistant", content=(TextPart(text="hi"),),
                      origin=Origin(provider="anthropic", api="messages", model="c",
                                    raw=({"type": "text", "text": "hi"},)))
    assert message_to_items(message, "openai") == [{"role": "assistant", "content": "hi"}]


# --- the client: one transport, the Responses path ---


def test_the_client_posts_to_responses_and_advertises_search() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body(MESSAGE))

    client = OpenAICompatClient(name="openai", model="gpt-x", base_url="https://x.test/v1",
                                headers={}, quirks=DEFAULT, timeout=httpx.Timeout(1.0),
                                api="responses")
    client._http = httpx.Client(base_url="https://x.test/v1",
                                transport=httpx.MockTransport(handler))
    response = client.complete(ModelRequest(messages=(), hosted_tools=(SEARCH,)))
    assert seen[0].url.path == "/v1/responses"
    assert response.content[0] == parse(body(MESSAGE)).content[0]
    assert (client.api, client.hosted_tools) == ("responses", frozenset({"web_search"}))


def test_completions_refuses_hosted_tools_instead_of_dropping_them() -> None:
    client = OpenAICompatClient(name="openrouter", model="m", base_url="https://x.test/v1",
                                headers={}, quirks=DEFAULT, timeout=httpx.Timeout(1.0))
    assert client.hosted_tools == frozenset()
    with pytest.raises(BadRequestError, match="Responses API"):
        client.complete(ModelRequest(messages=(), hosted_tools=(SEARCH,)))


def test_an_unknown_api_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown OpenAI api"):
        OpenAICompatClient(name="openai", model="m", base_url="https://x.test", headers={},
                           quirks=DEFAULT, timeout=httpx.Timeout(1.0),
                           api="chat")  # type: ignore[arg-type]
