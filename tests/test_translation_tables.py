"""Cross-adapter tables: the wire asymmetries, pinned in one place.

Adding a provider or a case means adding a row, not writing a new test.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic as sdk
import httpx
import httpx2
import pytest

from agentloop.errors import FailurePhase, ProviderError
from agentloop.providers import _openai_compat, _openai_responses, anthropic
from agentloop.transcript import Message, TextPart, ToolCallPart, ToolResultPart

QUIRKS = _openai_compat.Quirks()

ADAPTERS: dict[str, Callable[[Message], list[dict[str, Any]]]] = {
    "anthropic": lambda m: [anthropic.message_to_wire(m)],
    "completions": lambda m: _openai_compat.message_to_wire(m, QUIRKS),
    "responses": lambda m: _openai_responses.message_to_items(m),
}

TWO_RESULTS = Message(role="user", content=(
    ToolResultPart(call_id="c1", tool_name="read", content="a"),
    ToolResultPart(call_id="c2", tool_name="read", content="b"),
))

ASSISTANT_CALLS = Message(role="assistant", content=(
    TextPart(text="reading"),
    ToolCallPart(id="c1", tool_name="read", arguments={"path": "a"}),
    ToolCallPart(id="c2", tool_name="read", arguments={"path": "b"}),
))


def _call_ids(items: list[dict[str, Any]]) -> list[str]:
    """Every tool-call/result id on the wire, in order, whatever the vendor calls it."""
    ids: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("tool_call_id", "tool_use_id", "call_id", "id"):
                value = node.get(key)
                if isinstance(value, str) and value in {"c1", "c2"}:
                    ids.append(value)
                    break
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(items)
    return ids


@pytest.mark.parametrize(
    ("adapter", "messages_out"),
    [("anthropic", 1), ("completions", 2), ("responses", 2)],
)
def test_two_tool_results_fan_out_as_each_vendor_requires(
    adapter: str, messages_out: int
) -> None:
    """Anthropic: one message, two blocks. The others: one item per result."""
    out = ADAPTERS[adapter](TWO_RESULTS)
    assert len(out) == messages_out


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
@pytest.mark.parametrize("message", [TWO_RESULTS, ASSISTANT_CALLS], ids=["results", "calls"])
def test_call_order_survives_every_translation(adapter: str, message: Message) -> None:
    """Results must come back in call order; calls must go out in call order."""
    assert _call_ids(ADAPTERS[adapter](message)) == ["c1", "c2"]


# --- every error raised after the server answered says so ---

ANTHROPIC_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _anthropic_message(*blocks: SimpleNamespace, stop: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks), stop_reason=stop, model="m", id="msg_1",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


def _responses_body(**fields: Any) -> dict[str, Any]:
    return {"model": "m", "status": "completed", "output": [], **fields}


ANSWERED: dict[str, Callable[[], object]] = {
    "completions/error-in-200": lambda: _openai_compat.response_from_wire(
        {"error": {"code": 503, "message": "down"}}, provider="p", quirks=QUIRKS
    ),
    "completions/error-in-200-no-code": lambda: _openai_compat.response_from_wire(
        {"error": "odd"}, provider="p", quirks=QUIRKS
    ),
    "completions/no-choices": lambda: _openai_compat.response_from_wire(
        {"choices": []}, provider="p", quirks=QUIRKS
    ),
    "completions/bad-shape": lambda: _openai_compat.response_from_wire(
        {"choices": ["not a dict"]}, provider="p", quirks=QUIRKS
    ),
    "completions/status": lambda: _openai_compat.classify_http_error(
        httpx.Response(503, json={"error": {"message": "down"}}), provider="p", quirks=QUIRKS
    ),
    "responses/failed-retryable": lambda: _openai_responses.response_from_wire(
        _responses_body(status="failed", error={"code": "server_error", "message": "x"}),
        provider="p", quirks=QUIRKS,
    ),
    "responses/failed": lambda: _openai_responses.response_from_wire(
        _responses_body(status="failed", error={"code": "invalid_prompt", "message": "x"}),
        provider="p", quirks=QUIRKS,
    ),
    "responses/bad-status": lambda: _openai_responses.response_from_wire(
        _responses_body(status="in_progress"), provider="p", quirks=QUIRKS
    ),
    "responses/bad-shape": lambda: _openai_responses.response_from_wire(
        _responses_body(output=["not a dict"]), provider="p", quirks=QUIRKS
    ),
    "anthropic/context-overflow-200": lambda: anthropic.response_from_wire(
        _anthropic_message(stop="model_context_window_exceeded")
    ),
    "anthropic/unreadable-block": lambda: anthropic.response_from_wire(
        _anthropic_message(SimpleNamespace(type="tool_use", id="c1"))
    ),
    "anthropic/empty-turn": lambda: anthropic.response_from_wire(_anthropic_message()),
    "anthropic/status": lambda: anthropic.classify_sdk_error(
        sdk.APIStatusError(
            "down", response=httpx2.Response(529, request=ANTHROPIC_REQUEST), body=None
        )
    ),
    "anthropic/unparseable-2xx": lambda: anthropic.classify_sdk_error(
        sdk.APIResponseValidationError(
            response=httpx2.Response(200, request=ANTHROPIC_REQUEST), body=None
        )
    ),
}


def _raised(build: Callable[[], object]) -> ProviderError:
    """The error a builder raises, or returns (the classifiers return theirs)."""
    try:
        result = build()
    except ProviderError as exc:
        return exc
    assert isinstance(result, ProviderError), f"neither raised nor returned an error: {result}"
    return result


@pytest.mark.parametrize("case", sorted(ANSWERED))
def test_an_error_after_the_server_answered_says_it_was_sent(case: str) -> None:
    """The server replied, so the request was sent and the phase is known."""
    error = _raised(ANSWERED[case])
    assert error.request_sent is True
    assert error.phase in (FailurePhase.STATUS, FailurePhase.BODY)
