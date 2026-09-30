import logging
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from agentloop.errors import (
    AuthError,
    BadRequestError,
    ConfigurationError,
    ContextLengthExceeded,
    ModelNotFound,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    ResponseInvalid,
)
from agentloop.provider import HostedTool, ModelRequest, StopReason, ToolSchema
from agentloop.providers.anthropic import (
    AnthropicClient,
    classify_sdk_error,
    message_to_wire,
    request_to_wire,
    response_from_wire,
    tool_to_wire,
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

MODEL = "claude-opus-5"
EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def wire(**kwargs: object) -> dict[str, Any]:
    """Build a request and translate it, in one step."""
    return request_to_wire(ModelRequest(messages=(), **kwargs), model=MODEL)  # type: ignore[arg-type]


def fake_message(
    *blocks: SimpleNamespace, stop_reason: str = "end_turn"
) -> SimpleNamespace:
    """Stand in for an SDK Message. Our code only reads attributes off it."""
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model=MODEL,
        id="msg_1",
    )


# --- the request ---


def test_max_tokens_is_always_sent() -> None:
    assert "max_tokens" in wire()          # Anthropic requires it


def test_temperature_is_never_forwarded() -> None:
    # the SDK has no such parameter, and the server 400s
    assert "temperature" not in wire(temperature=0.5)


def test_system_is_top_level_not_a_message() -> None:
    payload = wire(system="be brief")
    assert payload["system"] == "be brief"
    assert all(m["role"] != "system" for m in payload["messages"])


def test_strict_is_top_level_on_the_tool() -> None:
    tool = tool_to_wire(
        ToolSchema(name="read", description="Read a file.", parameters=EMPTY_SCHEMA)
    )
    assert tool["strict"] is True            # (OpenAI-compatible nests it inside `function`)
    assert tool["input_schema"] == EMPTY_SCHEMA


READ = ToolSchema(name="read", description="Read a file.", parameters=EMPTY_SCHEMA)


def test_disabling_parallel_calls_sets_tool_choice() -> None:
    payload = wire(tools=(READ,), parallel_tool_calls=False)
    assert payload["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_the_default_tool_choice_is_omitted() -> None:
    assert "tool_choice" not in wire(tools=(READ,))


@pytest.mark.parametrize(("choice", "wire_type"), [("required", "any"), ("none", "none")])
def test_tool_choice_is_translated(choice: str, wire_type: str) -> None:
    assert wire(tools=(READ,), tool_choice=choice)["tool_choice"] == {"type": wire_type}


def test_none_never_carries_the_parallel_flag() -> None:
    payload = wire(tools=(READ,), tool_choice="none", parallel_tool_calls=False)
    assert payload["tool_choice"] == {"type": "none"}


def test_no_tool_choice_without_tools() -> None:
    # the API rejects tool_choice when there are no tools to choose from
    assert "tool_choice" not in wire(tool_choice="required", parallel_tool_calls=False)


# --- messages ---


def test_tool_results_stay_in_one_message() -> None:
    payload = message_to_wire(
        Message(
            role="user",
            content=(
                ToolResultPart(call_id="c1", tool_name="read", content="a"),
                ToolResultPart(call_id="c2", tool_name="read", content="b"),
            ),
        )
    )
    assert payload["role"] == "user"
    assert len(payload["content"]) == 2      # one message, two blocks


def test_thinking_blocks_are_replayed_verbatim() -> None:
    original = {"type": "thinking", "thinking": "", "signature": "ErUBCkYIBRgC"}
    payload = message_to_wire(
        Message(role="assistant", content=(ThinkingPart(text="", raw=original),))
    )
    assert payload["content"][0] is original     # not reconstructed


def test_an_ordered_turn_keeps_its_order() -> None:
    payload = message_to_wire(
        Message(
            role="assistant",
            content=(
                TextPart(text="checking"),
                ToolCallPart(id="c1", tool_name="read", arguments={"path": "a.py"}),
            ),
        )
    )
    assert [b["type"] for b in payload["content"]] == ["text", "tool_use"]


# --- the response ---


def test_tool_calls_force_the_tool_use_stop_reason() -> None:
    message = fake_message(
        SimpleNamespace(type="tool_use", id="c1", name="read", input={"path": "a.py"}),
        stop_reason="end_turn",              # the wire says end_turn...
    )
    response = response_from_wire(message)
    assert response.stop_reason is StopReason.TOOL_USE    # ...Rule 1 overrides it
    assert response.raw_stop_reason == "end_turn"


def test_unknown_stop_reason_is_not_end_turn() -> None:
    message = fake_message(
        SimpleNamespace(type="text", text="hi"), stop_reason="some_future_reason"
    )
    assert response_from_wire(message).stop_reason is StopReason.UNKNOWN


def test_tool_arguments_arrive_already_parsed() -> None:
    message = fake_message(
        SimpleNamespace(type="tool_use", id="c1", name="read", input={"path": "a.py"})
    )
    call = response_from_wire(message).content[0]
    assert isinstance(call, ToolCallPart)
    assert call.arguments == {"path": "a.py"}    # no json.loads anywhere


def test_unknown_block_types_are_skipped_not_fatal() -> None:
    message = fake_message(
        SimpleNamespace(type="text", text="hi"),
        SimpleNamespace(type="some_future_block"),
    )
    assert len(response_from_wire(message).content) == 1


def test_context_overflow_raises_even_though_it_is_a_200() -> None:
    message = fake_message(
        SimpleNamespace(type="text", text=""),
        stop_reason="model_context_window_exceeded",
    )
    with pytest.raises(ContextLengthExceeded):
        response_from_wire(message)


def test_an_empty_turn_becomes_response_invalid() -> None:
    # ModelResponse forbids empty content; we convert pydantic's error to ours
    with pytest.raises(ResponseInvalid):
        response_from_wire(fake_message())


def test_unreported_cache_usage_stays_none() -> None:
    usage = response_from_wire(fake_message(SimpleNamespace(type="text", text="hi"))).usage
    assert usage.input_tokens == 10
    assert usage.cache_read_tokens is None


# --- truncation survives Rule 1 ---


def test_a_call_cut_off_by_max_tokens_stays_max_tokens() -> None:
    message = fake_message(
        SimpleNamespace(type="tool_use", id="c1", name="read", input={}),
        stop_reason="max_tokens",
    )
    assert response_from_wire(message).stop_reason is StopReason.MAX_TOKENS


# --- SDK errors -> the provider-independent contract ---

# The SDK is built on httpx2, not httpx: its errors carry httpx2 objects.
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(status: int, message: str = "nope", **headers: str) -> Exception:
    response = httpx2.Response(
        status, request=REQUEST, headers={"request-id": "req_1", **headers}
    )
    return anthropic.APIStatusError(message, response=response, body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(401), AuthError),
        (status_error(403), AuthError),
        (status_error(404), ModelNotFound),
        (status_error(408), ProviderTimeout),
        (status_error(409), ProviderUnavailable),
        (status_error(429), RateLimited),
        (status_error(400), BadRequestError),
        (status_error(400, "prompt is too long: 250000 tokens"), ContextLengthExceeded),
        (status_error(500), ProviderUnavailable),
        (status_error(529), ProviderUnavailable),
        (status_error(418), ProviderError),
        (anthropic.APITimeoutError(request=REQUEST), ProviderTimeout),
        (anthropic.APIConnectionError(request=REQUEST), ProviderUnavailable),
    ],
)
def test_sdk_errors_are_classified(error: Exception, expected: type[ProviderError]) -> None:
    classified = classify_sdk_error(error)
    assert type(classified) is expected
    assert classified.provider == "anthropic"


def test_a_classified_error_keeps_status_request_id_and_retry_after() -> None:
    error = classify_sdk_error(status_error(429, **{"retry-after": "7"}))
    assert (error.status_code, error.request_id, error.retry_after) == (429, "req_1", 7.0)
    assert error.retryable


def test_the_client_raises_the_contract_not_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(api_key="sk-ant-test")

    def refuse(**payload: object) -> None:
        raise status_error(529, "overloaded")

    monkeypatch.setattr(client._client.messages, "create", refuse)
    with pytest.raises(ProviderUnavailable) as caught:
        client.complete(ModelRequest(messages=()))
    assert isinstance(caught.value.__cause__, anthropic.APIStatusError)


def test_unresolvable_credentials_raise_auth_error_not_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SDK defers credential resolution to per-request header validation,
    # which raises a bare TypeError -- not an APIError -- when nothing was ever
    # configured (no api_key, no env var, no CLI profile).
    client = AnthropicClient(api_key="sk-ant-test")

    def unresolved(**payload: object) -> None:
        raise TypeError(
            '"Could not resolve authentication method. Expected one of api_key, '
            'auth_token, or credentials to be set. Or for one of the `X-Api-Key` '
            'or `Authorization` headers to be explicitly omitted"'
        )

    monkeypatch.setattr(client._client.messages, "create", unresolved)
    with pytest.raises(AuthError) as caught:
        client.complete(ModelRequest(messages=()))
    assert isinstance(caught.value.__cause__, TypeError)


def test_an_unrelated_type_error_is_not_mistaken_for_a_credential_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AnthropicClient(api_key="sk-ant-test")

    def broken(**payload: object) -> None:
        raise TypeError("unexpected keyword argument 'foo'")

    monkeypatch.setattr(client._client.messages, "create", broken)
    with pytest.raises(TypeError, match="unexpected keyword"):
        client.complete(ModelRequest(messages=()))


def test_the_sdk_does_not_retry_on_its_own() -> None:
    # RetryingClient owns retries; two layers would multiply attempts.
    assert AnthropicClient(api_key="sk-ant-test")._client.max_retries == 0


def test_close_is_idempotent() -> None:
    with AnthropicClient(api_key="sk-ant-test") as client:
        pass
    client.close()


# --- hosted web search: the request ---


def test_hosted_search_uses_basic_search_by_default() -> None:
    """Basic search keeps citations; dynamic filtering loses them (measured live)."""
    tool = HostedTool(kind="web_search", options={
        "allowed_domains": ["europa.eu"], "max_uses": 3,
        "user_location": {"country": "BE", "city": "Brussels"},
    })
    [wire_tool] = wire(hosted_tools=(tool,))["tools"]
    assert wire_tool == {
        "type": "web_search_20250305", "name": "web_search",
        "allowed_domains": ["europa.eu"], "max_uses": 3,
        "user_location": {"type": "approximate", "country": "BE", "city": "Brussels"},
    }


def test_dynamic_filtering_is_an_explicit_opt_in() -> None:
    tool = HostedTool(kind="web_search", options={"dynamic_filtering": True})
    assert wire(hosted_tools=(tool,))["tools"][0]["type"] == "web_search_20260209"
    off = HostedTool(kind="web_search", options={"dynamic_filtering": False})
    assert wire(hosted_tools=(off,))["tools"][0]["type"] == "web_search_20250305"


def test_function_and_hosted_tools_share_the_tools_list() -> None:
    payload = wire(tools=(READ,), hosted_tools=(HostedTool(kind="web_search"),))
    assert [t["name"] for t in payload["tools"]] == ["read", "web_search"]


def test_openai_only_tuning_is_logged_not_sent(caplog: pytest.LogCaptureFixture) -> None:
    tool = HostedTool(kind="web_search", options={"search_context_size": "high"})
    with caplog.at_level(logging.WARNING):
        [wire_tool] = wire(hosted_tools=(tool,))["tools"]
    assert "search_context_size" not in wire_tool
    assert "search_context_size" in caplog.text


def test_an_unsupported_hosted_kind_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="code_interpreter"):
        wire(hosted_tools=(HostedTool(kind="code_interpreter"),))


# --- hosted web search: the response (the documented example) ---

SEARCH_BLOCKS: list[dict[str, Any]] = [
    {"type": "text", "text": "I'll search for when Claude Shannon was born."},
    {"type": "server_tool_use", "id": "srvtoolu_01", "name": "web_search",
     "input": {"query": "claude shannon birth date"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_01", "content": [
        {"type": "web_search_result", "url": "https://en.wikipedia.org/wiki/Claude_Shannon",
         "title": "Claude Shannon - Wikipedia", "encrypted_content": "EqgfCioI...",
         "page_age": "April 30, 2025"},
    ]},
    {"type": "text", "text": "Claude Shannon was born on April 30, 1916",
     "citations": [{"type": "web_search_result_location",
                    "url": "https://en.wikipedia.org/wiki/Claude_Shannon",
                    "title": "Claude Shannon - Wikipedia", "encrypted_index": "Eo8BCioI...",
                    "cited_text": "Claude Elwood Shannon (April 30, 1916 - ..."}]},
]


def search_message(blocks: list[dict[str, Any]], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks, stop_reason=stop_reason, model=MODEL, id="msg_1",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def test_a_search_becomes_one_hosted_part_with_its_sources() -> None:
    response = response_from_wire(search_message(SEARCH_BLOCKS))
    kinds = [type(part).__name__ for part in response.content]
    assert kinds == ["TextPart", "HostedToolPart", "TextPart"]  # the result merged in
    search = response.content[1]
    assert isinstance(search, HostedToolPart)
    assert (search.kind, search.query, search.error) == (
        "web_search", "claude shannon birth date", None
    )
    assert [s.title for s in search.sources] == ["Claude Shannon - Wikipedia"]


def test_cited_text_becomes_a_citation() -> None:
    answer = response_from_wire(search_message(SEARCH_BLOCKS)).content[-1]
    assert isinstance(answer, TextPart)
    [citation] = answer.citations
    assert citation.cited_text is not None and citation.cited_text.startswith("Claude Elwood")


def test_an_error_result_is_an_object_not_a_list() -> None:
    blocks: list[dict[str, Any]] = [
        {"type": "server_tool_use", "id": "srvtoolu_02", "name": "web_search",
         "input": {"query": "q"}},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_02",
         "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
        {"type": "text", "text": "I could not search further."},
    ]
    search = response_from_wire(search_message(blocks)).content[0]
    assert isinstance(search, HostedToolPart)
    assert (search.error, search.sources) == ("max_uses_exceeded", ())


def test_a_result_for_an_earlier_call_still_becomes_a_part() -> None:
    """A mixed client/server turn runs the search on the NEXT request, whose
    response opens with a result for a call it does not repeat."""
    blocks = [SEARCH_BLOCKS[2], {"type": "text", "text": "Done."}]
    search = response_from_wire(search_message(blocks)).content[0]
    assert isinstance(search, HostedToolPart)
    assert (search.call_id, search.query) == ("srvtoolu_01", None)
    assert len(search.sources) == 1


def test_dynamic_filtering_blocks_are_kept_for_replay() -> None:
    """Code execution blocks the parts do not model survive in the origin."""
    blocks: list[dict[str, Any]] = [
        {"type": "server_tool_use", "id": "srvtoolu_c", "name": "code_execution",
         "input": {"code": "filter(results)"}},
        {"type": "bash_code_execution_tool_result", "tool_use_id": "srvtoolu_c",
         "content": {"type": "bash_code_execution_result", "stdout": "ok", "stderr": "",
                     "return_code": 0}},
        {"type": "container_upload", "file_id": "f_1"},
        {"type": "text", "text": "Filtered."},
    ]
    response = response_from_wire(search_message(blocks))
    assert response.origin is not None
    assert list(response.origin.raw) == blocks            # nothing dropped
    assert isinstance(response.content[0], HostedToolPart)


def test_a_successful_code_execution_result_is_not_an_error() -> None:
    """Seen live: dynamic filtering returns an OBJECT that is not an error."""
    blocks: list[dict[str, Any]] = [
        {"type": "server_tool_use", "id": "srvtoolu_c", "name": "code_execution",
         "input": {}},
        {"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_c",
         "content": {"type": "encrypted_code_execution_result", "encrypted_stdout": "x"}},
        {"type": "text", "text": "Done."},
    ]
    run = response_from_wire(search_message(blocks)).content[0]
    assert isinstance(run, HostedToolPart)
    assert (run.kind, run.error) == ("code_execution", None)


def test_pause_turn_is_paused_not_the_end() -> None:
    response = response_from_wire(search_message(SEARCH_BLOCKS[:2], stop_reason="pause_turn"))
    assert response.stop_reason is StopReason.PAUSED


# --- replay: exactly as received, or rebuilt from the parts ---


def test_a_turn_from_anthropic_is_replayed_exactly() -> None:
    response = response_from_wire(search_message(SEARCH_BLOCKS))
    message = Message(role="assistant", content=response.content, origin=response.origin)
    assert message_to_wire(message) == {"role": "assistant", "content": SEARCH_BLOCKS}


def test_a_turn_from_another_provider_is_rebuilt_from_its_parts() -> None:
    message = Message(
        role="assistant",
        content=(HostedToolPart(kind="web_search", query="q"), TextPart(text="Answer.")),
        origin=Origin(provider="openai", api="responses", model="gpt-x", raw=({},)),
    )
    assert message_to_wire(message) == {
        "role": "assistant", "content": [{"type": "text", "text": "Answer."}],
    }
