"""Golden transcript: pins serialization, not behaviour.

Driven by FakeClient and an inline tool, so it needs no network and no filesystem
beyond reading the committed snapshot. Regenerate with UPDATE_GOLDEN=1 and READ THE
DIFF before committing: reflexive regeneration is how a golden test stops testing.
"""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agentloop.loop import run_conversation
from agentloop.provider import ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import ContentPart, TextPart, ToolCallPart, Transcript

SNAPSHOT = Path(__file__).parent / "golden" / "two_turn_tool_use.json"


class Deps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def shout(deps: Deps, text: str) -> str:
    return text.upper()


def reply(*parts: ContentPart, stop: StopReason = StopReason.END_TURN) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts), stop_reason=stop,
        usage=Usage(input_tokens=10, output_tokens=5), model="fake-1", provider="fake",
    )


def test_golden_two_turn_tool_conversation() -> None:
    registry = Registry([ToolSpec(
        name="shout", description="Upper-case the text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "anything"}},
            "required": ["text"], "additionalProperties": False,
        },
        handler=shout,
    )])
    client = FakeClient(
        reply(ToolCallPart(id="c1", tool_name="shout", arguments={"text": "hi"}),
              stop=StopReason.TOOL_USE),
        reply(TextPart(text="It says HI.")),
    )
    transcript = Transcript()
    transcript.add_user_message("shout hi")
    run_conversation(transcript, client, registry, Deps())

    actual = transcript.model_dump_json(indent=2)
    if os.environ.get("UPDATE_GOLDEN"):
        SNAPSHOT.write_text(actual + "\n")
    # Compare PARSED models: key order and whitespace are not the contract.
    assert Transcript.model_validate_json(actual) == Transcript.model_validate_json(
        SNAPSHOT.read_text()
    )
