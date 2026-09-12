import pytest
from pydantic import ValidationError

from harness.errors import TranscriptError
from harness.transcript import (
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
)

# --- the cross-message rules (Part 4) ---


def test_missing_tool_result_is_caught_locally() -> None:
    transcript = Transcript()
    transcript.add_user_text("read a.py and b.py")
    transcript.add_assistant([
        TextPart(text="On it."),
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
        ToolCallPart(id="c2", name="read", arguments={"path": "b.py"}),
    ])
    with pytest.raises(TranscriptError, match=r"missing=\['c2'\]"):
        transcript.add_tool_results([
            ToolResultPart(call_id="c1", name="read", content="..."),
        ])


def test_results_are_reordered_to_call_order() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([
        ToolCallPart(id="c1", name="read", arguments={}),
        ToolCallPart(id="c2", name="read", arguments={}),
    ])
    # results arrive out of order, as they will from a thread pool in chapter 6
    transcript.add_tool_results([
        ToolResultPart(call_id="c2", name="read", content="second"),
        ToolResultPart(call_id="c1", name="read", content="first"),
    ])
    results_message = transcript.messages[-1]
    call_ids = [p.call_id for p in results_message.content
                if isinstance(p, ToolResultPart)]
    assert call_ids == ["c1", "c2"]


def test_unanswered_calls_are_rejected_by_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([ToolCallPart(id="c1", name="read", arguments={})])
    with pytest.raises(TranscriptError, match="unanswered"):
        transcript.check_invariants()


def test_a_complete_turn_passes_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([ToolCallPart(id="c1", name="read", arguments={})])
    transcript.add_tool_results([
        ToolResultPart(call_id="c1", name="read", content="x = 1"),
    ])
    transcript.check_invariants()          # does not raise


# --- the per-object rules pydantic now enforces ---

def test_an_empty_message_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        Message(role="user", content=())


def test_an_empty_tool_call_id_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        ToolCallPart(id="", name="read", arguments={})


def test_a_typo_is_an_error_not_a_silent_drop() -> None:
    with pytest.raises(ValidationError):
        # mypy catches this statically too, which is the point — the ignore is
        # what lets us also prove the RUNTIME behaviour.
        TextPart(text="hi", txt="typo")  # type: ignore[call-arg]


def test_parts_are_immutable() -> None:
    part = TextPart(text="hi")
    with pytest.raises(ValidationError):
        part.text = "bye"


# --- round trip (Part 5) ---

def test_a_whole_transcript_survives_json() -> None:
    original = Transcript()
    original.add_user_text("go")
    original.add_assistant([
        ThinkingPart(text="", signature="ErUBCkYIBRgC...", provider="anthropic",
                     model="claude-opus-5", raw={"type": "thinking"}),
        TextPart(text="checking"),
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
    ])
    original.add_tool_results([
        ToolResultPart(call_id="c1", name="read", content="x = 1"),
    ])

    restored = Transcript.model_validate_json(original.model_dump_json())
    assert restored == original


def test_the_discriminator_rebuilds_the_right_class() -> None:
    restored = Message.model_validate_json(
        '{"role":"assistant","content":[{"type":"tool_call","id":"c1",'
        '"name":"read","arguments":{}}]}'
    )
    assert isinstance(restored.content[0], ToolCallPart)


def test_an_unknown_part_type_fails_loudly() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Message.model_validate_json(
            '{"role":"user","content":[{"type":"video","url":"x"}]}'
        )
