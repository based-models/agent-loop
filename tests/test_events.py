"""The event trace: metadata by default, content only through a redactor."""

import json
import logging
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from agentloop.errors import AuthError, ConfigurationError, ProviderUnavailable, ToolError
from agentloop.events import EventTraceObserver
from agentloop.loop import run_conversation
from agentloop.provider import ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.retry import RetryingClient, RetryPolicy
from agentloop.summary import SummaryOptions
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import ContentPart, TextPart, ToolCallPart, Transcript

SECRET = "zq9-top-secret-value"


class ToyDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def echo(deps: ToyDeps, text: str) -> str:
    return f"echoed: {text}"


def boom(deps: ToyDeps) -> str:
    raise ToolError(f"failed with {SECRET}")


def _spec(name: str, handler: object, **props: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"the {name} tool",
        parameters={
            "type": "object",
            "properties": {k: {"type": v} for k, v in props.items()},
            "required": list(props),
            "additionalProperties": False,
        },
        handler=handler,  # type: ignore[arg-type]
    )


REGISTRY = Registry([_spec("echo", echo, text="string"), _spec("boom", boom)])


def resp(*parts: ContentPart, stop: StopReason = StopReason.END_TURN) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts),
        stop_reason=stop,
        usage=Usage(input_tokens=3, output_tokens=2),
        model="fake-1",
        provider="fake",
    )


def echo_call(call_id: str, text: str = SECRET) -> ToolCallPart:
    return ToolCallPart(id=call_id, tool_name="echo", arguments={"text": text})


def run(
    client: object,
    trace: EventTraceObserver,
    *,
    prompt: str = SECRET,
    **kwargs: Any,
) -> None:
    transcript = Transcript()
    transcript.add_user_message(prompt)
    run_conversation(transcript, client, REGISTRY, ToyDeps(), observer=trace, **kwargs)  # type: ignore[arg-type]


def traced(
    client: object, *, prompt: str = SECRET, **options: Any
) -> tuple[list[dict[str, Any]], str]:
    lines: list[str] = []
    trace = EventTraceObserver(lines.append, terminator="", **options)
    run(client, trace, prompt=prompt, system=f"you are careful with {SECRET}")
    return [json.loads(line) for line in lines], "\n".join(lines)


def script() -> FakeClient:
    return FakeClient(
        resp(echo_call("a"), ToolCallPart(id="b", tool_name="boom", arguments={}),
             stop=StopReason.TOOL_USE),
        resp(TextPart(text=f"the answer involves {SECRET}")),
    )


# --- by default: metadata only ---


def test_every_line_is_a_json_object_in_sequence() -> None:
    records, _ = traced(script())
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    assert records[0]["event"] == "conversation_start"
    assert records[-1]["event"] == "conversation_end"
    kinds = [r["event"] for r in records]
    assert kinds.count("request") == 2 and kinds.count("response") == 2
    assert kinds.count("tool_start") == kinds.count("tool_end") == 2


def test_no_content_is_written_by_default() -> None:
    _, text = traced(script())
    assert SECRET not in text


def test_the_metadata_that_is_written_is_useful() -> None:
    records, _ = traced(script())
    request = next(r for r in records if r["event"] == "request")
    response = next(r for r in records if r["event"] == "response")
    assert request["tools"] == ["echo", "boom"]
    assert response["input_tokens"] == 3
    assert response["tool_calls"] == ["echo", "boom"]
    ends = [r for r in records if r["event"] == "tool_end"]
    assert {r["tool_name"]: r["is_error"] for r in ends} == {"echo": False, "boom": True}
    assert all(r["result_bytes"] > 0 and r["duration_seconds"] >= 0 for r in ends)


def test_every_line_after_the_start_carries_the_conversation_ids() -> None:
    lines: list[str] = []
    run(
        script(),
        EventTraceObserver(lines.append, terminator=""),
        conversation_id="conv-1", correlation_id="round-42", parent_id="p-1",
    )
    for line in lines:
        record = json.loads(line)
        assert record["conversation_id"] == "conv-1"
        assert record["correlation_id"] == "round-42"
        assert record["parent_id"] == "p-1"


def test_the_summary_is_written_as_the_last_line_on_failure_too() -> None:
    lines: list[str] = []
    with pytest.raises(AuthError):
        run(FakeClient(AuthError("bad key")), EventTraceObserver(lines.append, terminator=""))
    last = json.loads(lines[-1])
    assert last["event"] == "conversation_end"
    assert last["summary"]["status"] == "provider_error"


def test_stalls_and_budget_warnings_are_events() -> None:
    lines: list[str] = []
    client = FakeClient(
        *[resp(echo_call(f"c{i}"), stop=StopReason.TOOL_USE) for i in range(4)],
        resp(TextPart(text="done")),
    )
    run(
        client,
        EventTraceObserver(lines.append, terminator=""),
        max_iterations=10,
        summary_options=SummaryOptions(stall_threshold=3, warn_at=(0.3,)),
    )
    events = {json.loads(line)["event"]: json.loads(line) for line in lines}
    assert events["stall"]["tool_name"] == "echo" and events["stall"]["count"] == 3
    assert events["budget_warning"]["kind"] == "iterations"
    assert SECRET not in "\n".join(lines)


def test_the_terminator_is_appended_to_each_line() -> None:
    lines: list[str] = []
    run(FakeClient(resp(TextPart(text="x"))), EventTraceObserver(lines.append))
    assert all(line.endswith("\n") for line in lines)


# --- capturing content ---


def test_capturing_content_without_a_redactor_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="redact"):
        EventTraceObserver(lambda line: None, capture_content=True)


def test_capturing_without_a_redactor_is_possible_if_asked_for_explicitly() -> None:
    records, text = traced(script(), capture_content=True, allow_unredacted=True)
    assert SECRET in text
    request = next(r for r in records if r["event"] == "request")
    assert request["new_messages"][0]["role"] == "user"
    assert request["system"] == f"you are careful with {SECRET}"


def scrub(text: str) -> str:
    return text.replace(SECRET, "[REDACTED]")


def test_the_redactor_is_applied_to_everything_that_is_captured() -> None:
    records, text = traced(script(), capture_content=True, redact=scrub)
    assert SECRET not in text
    assert text.count("[REDACTED]") >= 6
    kinds = {r["event"] for r in records}
    assert {"request", "response", "tool_start", "tool_end"} <= kinds
    tool_start = next(r for r in records if r["event"] == "tool_start")
    assert tool_start["arguments"] == {"text": "[REDACTED]"}
    boom_end = next(
        r for r in records if r["event"] == "tool_end" and r["tool_name"] == "boom"
    )
    assert boom_end["content"] == "Error: failed with [REDACTED]"


def test_a_provider_error_message_is_scrubbed_when_a_retry_is_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    lines: list[str] = []
    trace = EventTraceObserver(
        lines.append, terminator="", capture_content=True, redact=scrub
    )
    flaky = FakeClient(ProviderUnavailable(f"503 {SECRET}"), resp(TextPart(text="ok")))
    client = RetryingClient(flaky, policy=RetryPolicy(base_delay=0.01), observer=trace)
    run(client, trace)
    text = "\n".join(lines)
    assert "retry" in text and SECRET not in text and "503 [REDACTED]" in text


def test_structure_the_developer_chose_is_not_redacted() -> None:
    records, _ = traced(script(), capture_content=True, redact=lambda text: "R")
    request = next(r for r in records if r["event"] == "request")
    message = request["new_messages"][0]
    assert message["role"] == "user"
    assert message["parts"][0]["type"] == "text"
    assert message["parts"][0]["content"]["text"] == "R"
    tool_start = next(r for r in records if r["event"] == "tool_start")
    assert tool_start["tool_name"] == "echo"


def test_a_redactor_that_fails_leaks_nothing() -> None:
    def broken(text: str) -> str:
        raise RuntimeError("scrubber down")

    _, text = traced(script(), capture_content=True, redact=broken)
    assert SECRET not in text


def test_redaction_happens_before_clipping_so_a_cut_cannot_expose_half_a_secret() -> None:
    records, text = traced(
        FakeClient(resp(TextPart(text=f"aa{SECRET}"))),
        capture_content=True,
        redact=scrub,
        max_string_chars=5,
    )
    assert "zq9" not in text
    response = next(r for r in records if r["event"] == "response")
    assert response["content"][0]["content"]["text"].startswith("aa[RE")
    assert "more chars" in response["content"][0]["content"]["text"]


def test_each_request_carries_only_the_messages_added_since_the_last() -> None:
    records, _ = traced(script(), capture_content=True, redact=scrub)
    first, second = [r for r in records if r["event"] == "request"]
    assert [m["role"] for m in first["new_messages"]] == ["user"]
    assert [m["role"] for m in second["new_messages"]] == ["assistant", "user"]
    assert "system" in first and "system" not in second  # the system prompt, once


# --- robustness ---


def test_a_sink_that_raises_does_not_fail_the_run(caplog: pytest.LogCaptureFixture) -> None:
    def down(line: str) -> None:
        raise OSError("disk full")

    with caplog.at_level(logging.ERROR):
        run(FakeClient(resp(TextPart(text="x"))), EventTraceObserver(down))
    assert "disk full" in caplog.text


def test_parallel_tools_still_produce_ordered_unique_sequence_numbers() -> None:
    client = FakeClient(
        resp(*[echo_call(f"c{i}", text=str(i)) for i in range(8)], stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    records, _ = traced(client)
    seqs = [r["seq"] for r in records]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert sum(r["event"] == "tool_end" for r in records) == 8


# --- a model call that failed for good ---


def test_a_failed_model_call_emits_model_error_without_its_message() -> None:
    from agentloop.errors import FailurePhase, ProviderUnreachable

    error = ProviderUnreachable(
        f"tls failed near {SECRET}", provider="p", phase=FailurePhase.TLS,
        request_sent=False, request_id="req-1",
    )
    lines: list[str] = []
    trace = EventTraceObserver(lines.append, terminator="")
    client = RetryingClient(FakeClient(error), policy=RetryPolicy(base_delay=0.01), observer=trace)
    with pytest.raises(ProviderUnreachable):
        run(client, trace)
    records = [json.loads(line) for line in lines]
    failed = next(r for r in records if r["event"] == "model_error")
    assert failed["error_type"] == "ProviderUnreachable"
    assert failed["phase"] == "tls"
    assert failed["request_sent"] is False  # False is a fact; only unknown is omitted
    assert failed["request_id"] == "req-1"
    assert failed["attempt_log"][0]["error_type"] == "ProviderUnreachable"
    assert "message" not in failed
    assert SECRET not in "\n".join(lines)
    assert [r["event"] for r in records].index("model_error") < [
        r["event"] for r in records
    ].index("conversation_end")


def test_an_unknown_send_state_is_absent_from_model_error() -> None:
    lines: list[str] = []
    trace = EventTraceObserver(lines.append, terminator="")
    with pytest.raises(AuthError):
        run(FakeClient(AuthError("no")), trace)
    failed = next(json.loads(line) for line in lines if '"model_error"' in line)
    assert "request_sent" not in failed
    assert "phase" not in failed


def test_a_captured_model_error_message_is_redacted() -> None:
    lines: list[str] = []
    trace = EventTraceObserver(
        lines.append, terminator="", capture_content=True, redact=scrub
    )
    with pytest.raises(AuthError):
        run(FakeClient(AuthError(f"bad key {SECRET}")), trace)
    text = "\n".join(lines)
    assert SECRET not in text and "bad key [REDACTED]" in text
