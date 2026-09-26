import pytest
from pydantic import ValidationError

from agentloop.errors import TranscriptError
from agentloop.transcript import (
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
    from_jsonl,
    to_jsonl,
)

# --- the cross-message rules (Part 4) ---


def test_missing_tool_result_is_caught_locally() -> None:
    transcript = Transcript()
    transcript.add_user_message("read a.py and b.py")
    transcript.add_assistant_message([
        TextPart(text="On it."),
        ToolCallPart(id="c1", tool_name="read", arguments={"path": "a.py"}),
        ToolCallPart(id="c2", tool_name="read", arguments={"path": "b.py"}),
    ])
    with pytest.raises(TranscriptError, match=r"missing=\['c2'\]"):
        transcript.add_tool_results([
            ToolResultPart(call_id="c1", tool_name="read", content="..."),
        ])


def test_results_are_reordered_to_call_order() -> None:
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message([
        ToolCallPart(id="c1", tool_name="read", arguments={}),
        ToolCallPart(id="c2", tool_name="read", arguments={}),
    ])
    # results arrive out of order, as they will from a thread pool in chapter 6
    transcript.add_tool_results([
        ToolResultPart(call_id="c2", tool_name="read", content="second"),
        ToolResultPart(call_id="c1", tool_name="read", content="first"),
    ])
    results_message = transcript.messages[-1]
    call_ids = [p.call_id for p in results_message.content
                if isinstance(p, ToolResultPart)]
    assert call_ids == ["c1", "c2"]


def test_unanswered_calls_are_rejected_by_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message([ToolCallPart(id="c1", tool_name="read", arguments={})])
    with pytest.raises(TranscriptError, match="unanswered"):
        transcript.check_invariants()


def test_a_complete_turn_passes_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message([ToolCallPart(id="c1", tool_name="read", arguments={})])
    transcript.add_tool_results([
        ToolResultPart(call_id="c1", tool_name="read", content="x = 1"),
    ])
    transcript.check_invariants()          # does not raise


# --- the per-object rules pydantic now enforces ---

def test_an_empty_message_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        Message(role="user", content=())


def test_an_empty_tool_call_id_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        ToolCallPart(id="", tool_name="read", arguments={})


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
    original.add_user_message("go")
    original.add_assistant_message([
        ThinkingPart(text="", signature="ErUBCkYIBRgC...", provider="anthropic",
                     model="claude-opus-5", raw={"type": "thinking"}),
        TextPart(text="checking"),
        ToolCallPart(id="c1", tool_name="read", arguments={"path": "a.py"}),
    ])
    original.add_tool_results([
        ToolResultPart(call_id="c1", tool_name="read", content="x = 1"),
    ])

    restored = Transcript.model_validate_json(original.model_dump_json())
    assert restored == original


def test_the_discriminator_rebuilds_the_right_class() -> None:
    restored = Message.model_validate_json(
        '{"role":"assistant","content":[{"type":"tool_call","id":"c1",'
        '"tool_name":"read","arguments":{}}]}'
    )
    assert isinstance(restored.content[0], ToolCallPart)


def test_an_unknown_part_type_fails_loudly() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Message.model_validate_json(
            '{"role":"user","content":[{"type":"video","url":"x"}]}'
        )


# --- JSONL: a pair of pure functions, no I/O (Part 6) ---


def _round_trip_fixture() -> Transcript:
    transcript = Transcript()
    transcript.add_user_message("read a.py")
    transcript.add_assistant_message([
        TextPart(text="Looking."),
        ToolCallPart(id="c1", tool_name="read", arguments={"path": "a.py"}),
    ])
    transcript.add_tool_results([
        ToolResultPart(call_id="c1", tool_name="read", content="x = 1\ny = 2\n"),
    ])
    return transcript


def test_to_jsonl_writes_one_line_per_message() -> None:
    rendered = to_jsonl(_round_trip_fixture().messages)
    assert rendered.endswith("\n")
    assert len(rendered.splitlines()) == 3


def test_a_transcript_round_trips_through_jsonl() -> None:
    original = _round_trip_fixture()
    assert from_jsonl(to_jsonl(original.messages).splitlines()) == original


def test_no_line_contains_a_raw_newline() -> None:
    """The property that makes JSONL work at all.

    Every tool result holds newlines -- a file's contents, a command's stdout.
    If model_dump_json ever stopped escaping them, one result would silently
    become several messages on load.
    """
    rendered = to_jsonl(_round_trip_fixture().messages)
    assert "x = 1\ny = 2" not in rendered          # escaped, not literal
    assert "x = 1\\ny = 2" in rendered
    for line in rendered.splitlines():
        Message.model_validate_json(line)          # each line is complete on its own


def test_blank_and_trailing_lines_are_skipped() -> None:
    rendered = to_jsonl(_round_trip_fixture().messages)
    noisy = ["", *rendered.splitlines(), "", "   "]
    assert len(from_jsonl(noisy).messages) == 3


def test_a_truncated_final_line_fails_loudly() -> None:
    """A crashed run leaves a damaged log. Say so; do not silently drop a turn."""
    rendered = to_jsonl(_round_trip_fixture().messages)
    damaged = rendered[: len(rendered) - 20]       # chop the last line short
    with pytest.raises(ValidationError):
        from_jsonl(damaged.splitlines())


def test_an_unknown_part_type_in_an_old_log_fails_loudly() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        from_jsonl(['{"role":"user","content":[{"type":"video","url":"x"}]}'])


def test_a_thinking_signature_survives_byte_for_byte() -> None:
    """Anthropic rejects a replayed thinking block whose signature changed."""
    signature = "ErUBCkYIBBgCKkA+v/9xyz==\n\twith whitespace"
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message([
        ThinkingPart(text="hmm", signature=signature, provider="anthropic", model="m"),
    ])
    restored = from_jsonl(to_jsonl(transcript.messages).splitlines())
    part = restored.messages[1].content[0]
    assert isinstance(part, ThinkingPart)
    assert part.signature == signature


def test_from_jsonl_accepts_any_iterable_of_lines() -> None:
    rendered = to_jsonl(_round_trip_fixture().messages)
    generator = (line for line in rendered.splitlines())
    assert len(from_jsonl(generator).messages) == 3


def test_from_jsonl_does_not_check_invariants() -> None:
    """A log captured mid-turn must load. Checking is the caller's decision."""
    partial = Transcript()
    partial.add_user_message("go")
    partial.add_assistant_message([
        ToolCallPart(id="c1", tool_name="read", arguments={}),   # unanswered
    ])
    loaded = from_jsonl(to_jsonl(partial.messages).splitlines())
    assert len(loaded.messages) == 2
    with pytest.raises(TranscriptError):
        loaded.check_invariants()                                # ...if you ask


def test_the_transcript_module_touches_no_filesystem() -> None:
    """to_jsonl/from_jsonl used to open files. They do not any more, and this
    is what keeps `pathlib` off chapter 11's allowlist."""
    import ast
    from pathlib import Path as _Path

    source = _Path(__file__).parent.parent / "src" / "agentloop" / "transcript.py"
    roots = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not (roots & {"pathlib", "os", "io", "shutil"}), (
        f"transcript.py imports {sorted(roots & {'pathlib', 'os', 'io', 'shutil'})}"
    )


# --- exactly one result per call, and nothing else ---


def _called(*ids: str) -> Transcript:
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message(
        [ToolCallPart(id=i, tool_name="read", arguments={}) for i in ids]
    )
    return transcript


def _result(call_id: str) -> ToolResultPart:
    return ToolResultPart(call_id=call_id, tool_name="read", content="...")


def test_a_second_result_for_the_same_call_is_rejected() -> None:
    with pytest.raises(TranscriptError, match=r"more than one result.*c1"):
        _called("c1").add_tool_results([_result("c1"), _result("c1")])


def test_results_with_nothing_pending_are_a_transcript_error() -> None:
    transcript = Transcript()
    transcript.add_user_message("go")
    with pytest.raises(TranscriptError, match="no pending"):
        transcript.add_tool_results([])


def test_check_invariants_counts_results_not_just_ids() -> None:
    transcript = _called("c1")
    transcript.messages.append(Message(role="user", content=(_result("c1"), _result("c1"))))
    with pytest.raises(TranscriptError, match="answers"):
        transcript.check_invariants()


def test_check_invariants_wants_the_answer_from_the_user_role() -> None:
    transcript = _called("c1")
    transcript.messages.append(Message(role="assistant", content=(_result("c1"),)))
    with pytest.raises(TranscriptError, match="from the assistant"):
        transcript.check_invariants()


def test_check_invariants_rejects_an_orphan_result() -> None:
    transcript = Transcript()
    transcript.add_user_message("go")
    transcript.add_assistant_message([TextPart(text="no tools needed")])
    transcript.messages.append(Message(role="user", content=(_result("c9"),)))
    with pytest.raises(TranscriptError, match="does not directly follow"):
        transcript.check_invariants()


def test_check_invariants_rejects_a_result_in_the_first_message() -> None:
    transcript = Transcript(messages=[Message(role="user", content=(_result("c1"),))])
    with pytest.raises(TranscriptError, match="does not directly follow"):
        transcript.check_invariants()


def test_a_well_formed_transcript_still_passes() -> None:
    transcript = _called("c1", "c2")
    transcript.add_tool_results([_result("c2"), _result("c1")])
    transcript.add_assistant_message([TextPart(text="done")])
    transcript.check_invariants()
