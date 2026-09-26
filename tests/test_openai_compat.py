from typing import Any

import httpx
import pytest

from agentloop.errors import (
    AuthError,
    ContextLengthExceeded,
    InsufficientCredits,
    ResponseInvalid,
)
from agentloop.provider import ModelRequest, StopReason, ToolSchema
from agentloop.providers._openai_compat import (
    OpenAICompatClient,
    Quirks,
    classify_http_error,
    message_to_wire,
    parse_arguments,
    request_to_wire,
    response_from_wire,
    tool_to_wire,
)
from agentloop.transcript import Message, ToolCallPart, ToolResultPart

DEFAULT = Quirks()
EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def wire(**kwargs: object) -> dict[str, Any]:
    return request_to_wire(
        ModelRequest(messages=(), **kwargs),  # type: ignore[arg-type]
        model="m",
        quirks=DEFAULT,
    )


def body(
    finish_reason: str | None, message: dict[str, Any], **extra: object
) -> dict[str, Any]:
    """The minimum shape of a chat/completions response."""
    return {
        "choices": [{"finish_reason": finish_reason, "message": message}],
        "model": "m",
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        **extra,
    }


def http(status: int, **kwargs: object) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("POST", "http://x/v1/chat/completions"),
        **kwargs,  # type: ignore[arg-type]
    )


# --- the lossy direction (Part 2) ---


def test_one_user_message_fans_out_to_n_tool_messages() -> None:
    out = message_to_wire(
        Message(
            role="user",
            content=(
                ToolResultPart(call_id="c1", tool_name="read", content="a"),
                ToolResultPart(call_id="c2", tool_name="read", content="b"),
            ),
        ),
        DEFAULT,
    )
    assert [m["role"] for m in out] == ["tool", "tool"]
    assert [m["tool_call_id"] for m in out] == ["c1", "c2"]   # call order preserved


def test_assistant_with_no_text_sends_none_not_empty_string() -> None:
    out = message_to_wire(
        Message(
            role="assistant",
            content=(ToolCallPart(id="c1", tool_name="read", arguments={}),),
        ),
        DEFAULT,
    )
    assert out[0]["content"] is None


def test_raw_arguments_are_replayed_verbatim() -> None:
    call = ToolCallPart(
        id="c1",
        tool_name="read",
        arguments={},
        raw_arguments='{"path": "a.py"',       # truncated: what the model actually said
        parse_error="unterminated",
    )
    out = message_to_wire(Message(role="assistant", content=(call,)), DEFAULT)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"'


def test_exactly_one_system_message_and_it_is_first() -> None:
    messages = wire(system="be brief")["messages"]
    assert [m["role"] for m in messages].count("system") == 1
    assert messages[0]["role"] == "system"


def test_strict_goes_inside_function_not_top_level() -> None:
    tool = tool_to_wire(
        ToolSchema(name="read", description="d", parameters=EMPTY_SCHEMA), DEFAULT
    )
    assert tool["function"]["strict"] is True
    assert "strict" not in tool                # cf. chapter 4, where it IS top-level


def test_temperature_is_forwarded_here() -> None:
    # unlike Anthropic: these providers accept it
    assert wire(temperature=0.2)["temperature"] == 0.2


# --- parsing arguments (Part 4) ---


def test_malformed_arguments_keep_the_call_and_record_the_error() -> None:
    arguments, raw, error = parse_arguments('{"path": "a.py"')
    assert arguments == {}
    assert raw == '{"path": "a.py"'
    assert error is not None


def test_empty_arguments_string_is_not_an_error() -> None:
    assert parse_arguments("") == ({}, "", None)


def test_already_parsed_arguments_pass_through() -> None:
    assert parse_arguments({"path": "a.py"}) == ({"path": "a.py"}, None, None)


def test_non_object_arguments_are_an_error() -> None:
    _, _, error = parse_arguments("[1, 2, 3]")
    assert error is not None


# --- reading the response ---


def test_tool_calls_override_a_stop_finish_reason() -> None:
    response = response_from_wire(
        body(
            "stop",                             # the vLLM bug this rule exists for
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                    }
                ],
            },
        ),
        provider="test",
        quirks=DEFAULT,
    )
    assert response.stop_reason is StopReason.TOOL_USE
    assert response.raw_stop_reason == "stop"


def test_missing_tool_call_ids_are_generated() -> None:
    response = response_from_wire(
        body(
            "tool_calls",
            {
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "read", "arguments": "{}"}},
                    {"type": "function", "function": {"name": "read", "arguments": "{}"}},
                ],
            },
        ),
        provider="test",
        quirks=DEFAULT,
    )
    ids = [p.id for p in response.content if isinstance(p, ToolCallPart)]
    assert all(ids) and len(set(ids)) == 2      # non-empty and unique


def test_missing_usage_details_stay_none() -> None:
    usage = response_from_wire(
        body("stop", {"content": "hi", "tool_calls": None}),
        provider="test",
        quirks=Quirks(reports_usage_details=False),
    ).usage
    assert usage.input_tokens == 5              # reported
    assert usage.cache_read_tokens is None      # NOT reported, not zero


def test_an_empty_turn_becomes_response_invalid() -> None:
    with pytest.raises(ResponseInvalid):
        response_from_wire(
            body("stop", {"content": None, "tool_calls": None}),
            provider="test",
            quirks=DEFAULT,
        )


def test_an_error_inside_a_200_is_raised() -> None:
    with pytest.raises(Exception, match="upstream exploded"):
        response_from_wire(
            body("stop", {"content": "hi"}, error={"message": "upstream exploded"}),
            provider="openrouter",
            quirks=DEFAULT,
        )


# --- error classification (Part 4.5) ---


def test_modal_plain_text_401_does_not_crash_the_parser() -> None:
    response = http(401, text="modal-http: missing credentials for proxy authorization")
    error = classify_http_error(
        response, provider="modal", quirks=Quirks(errors_are_json=False)
    )
    assert isinstance(error, AuthError)
    assert "missing credentials" in str(error)   # the message survived


def test_vllm_context_overflow_is_found_in_the_message_text() -> None:
    response = http(
        400,
        json={
            "object": "error",
            "code": 400,                         # the INTEGER, not a string code
            "message": "This model's maximum context length is 8192 tokens. "
            "However, you requested 254457 tokens",
        },
    )
    error = classify_http_error(response, provider="modal", quirks=DEFAULT)
    assert isinstance(error, ContextLengthExceeded)


def test_negative_max_tokens_is_also_a_context_overflow() -> None:
    response = http(
        400, json={"message": "max_tokens must be at least 1, got -186"}
    )
    error = classify_http_error(response, provider="modal", quirks=DEFAULT)
    assert isinstance(error, ContextLengthExceeded)


def test_insufficient_credits_is_not_retryable() -> None:
    response = http(402, json={"error": {"message": "Insufficient credits"}})
    error = classify_http_error(response, provider="openrouter", quirks=DEFAULT)
    assert isinstance(error, InsufficientCredits)
    assert error.retryable is False


def test_rate_limit_honours_retry_after() -> None:
    response = http(429, json={"error": {"message": "slow down"}},
                    headers={"retry-after": "2.5"})
    error = classify_http_error(response, provider="openrouter", quirks=DEFAULT)
    assert error.retryable is True
    assert error.retry_after == 2.5


def test_html_error_page_does_not_crash_the_json_path() -> None:
    # a load balancer answered, not the provider
    response = http(502, text="<html><body>502 Bad Gateway</body></html>")
    error = classify_http_error(response, provider="openrouter", quirks=DEFAULT)
    assert error.retryable is True


# --- truncation survives Rule 1 ---


def test_a_call_cut_off_by_length_stays_max_tokens() -> None:
    response = response_from_wire(
        body("length", {"content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": '{"path": "a.'}},
        ]}),
        provider="test", quirks=DEFAULT,
    )
    assert response.stop_reason is StopReason.MAX_TOKENS


# --- malformed 200s become ResponseInvalid, never a raw Python error ---


def test_numeric_arguments_are_a_parse_error_not_a_crash() -> None:
    arguments, raw, error = parse_arguments(42)
    assert (arguments, raw) == ({}, "42")
    assert error is not None and "int" in error


def test_choices_of_the_wrong_shape_are_response_invalid() -> None:
    with pytest.raises(ResponseInvalid):
        response_from_wire({"choices": "nope", "model": "m"}, provider="test", quirks=DEFAULT)


def test_non_string_content_is_response_invalid() -> None:
    with pytest.raises(ResponseInvalid):
        response_from_wire(body("stop", {"content": 7}), provider="test", quirks=DEFAULT)


def client_answering(response: httpx.Response) -> OpenAICompatClient:
    client = OpenAICompatClient(
        name="test", model="m", base_url="https://x.test/v1", headers={},
        quirks=DEFAULT, timeout=httpx.Timeout(1.0),
    )
    client._http = httpx.Client(
        base_url="https://x.test/v1", transport=httpx.MockTransport(lambda _: response)
    )
    return client


def test_a_200_that_is_not_json_is_response_invalid() -> None:
    client = client_answering(httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(ResponseInvalid, match="not JSON"):
        client.complete(ModelRequest(messages=()))


def test_a_200_that_is_a_json_list_is_response_invalid() -> None:
    client = client_answering(httpx.Response(200, json=[1, 2]))
    with pytest.raises(ResponseInvalid, match="not an object"):
        client.complete(ModelRequest(messages=()))


def test_close_is_idempotent() -> None:
    with client_answering(httpx.Response(200, json={})) as client:
        pass
    client.close()


# --- generated ids stay unique ---


def test_a_generated_id_never_collides_with_a_real_one() -> None:
    response = response_from_wire(
        body("tool_calls", {"content": None, "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "read", "arguments": "{}"}}
            for i in ("x", "x_2", "x")
        ]}),
        provider="test", quirks=DEFAULT,
    )
    ids = [p.id for p in response.content if isinstance(p, ToolCallPart)]
    assert len(set(ids)) == 3, ids
