"""Cross-adapter tables: the wire asymmetries, pinned in one place.

Adding a provider or a case means adding a row, not writing a new test.
"""

from collections.abc import Callable
from typing import Any

import pytest

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
