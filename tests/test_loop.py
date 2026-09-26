import logging

import pytest
from pydantic import BaseModel, ConfigDict

from agentloop.errors import LoopBudgetExceeded
from agentloop.loop import ListObserver, run_conversation
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.providers._openai_compat import Quirks, response_from_wire
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import (
    ContentPart,
    TextPart,
    ToolCallPart,
    Transcript,
    final_text,
)


class ToyDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def echo(deps: ToyDeps, text: str) -> str:
    return f"echoed: {text}"


REGISTRY = Registry([
    ToolSpec(
        name="echo", description="Return the text you were given.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "anything"}},
            "required": ["text"], "additionalProperties": False,
        },
        handler=echo,
    )
])


class ScriptedClient:
    """Replays canned responses and records every request.

    Chapter 11 promotes this to `agentloop.providers.fake.FakeClient`.
    """

    name = "fake"
    model = "fake-1"

    def __init__(self, *responses: ModelResponse) -> None:
        self._queue = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(
                f"script exhausted after {len(self.requests)} requests; "
                "the loop asked for more than the test provides"
            )
        return self._queue.pop(0)


def resp(*parts: ContentPart, stop: StopReason = StopReason.END_TURN,
         raw: str | None = None) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts), stop_reason=stop, raw_stop_reason=raw,
        usage=Usage(input_tokens=1, output_tokens=1),
        model="fake-1", provider="fake",
    )


def echo_call(call_id: str, text: str) -> ToolCallPart:
    return ToolCallPart(id=call_id, tool_name="echo", arguments={"text": text})


def run(client: ScriptedClient, **kwargs: object) -> Transcript:
    transcript = Transcript()
    transcript.add_user_message("go")
    return run_conversation(
        transcript, client, REGISTRY, ToyDeps(), **kwargs  # type: ignore[arg-type]
    )


# --- the happy paths ---


def test_terminates_on_end_turn() -> None:
    transcript = run(ScriptedClient(resp(TextPart(text="done"))))
    assert [m.role for m in transcript.messages] == ["user", "assistant"]


def test_a_full_tool_round_trip() -> None:
    client = ScriptedClient(
        resp(echo_call("c1", "hello"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="it said hello")),
    )
    transcript = run(client)
    assert [m.role for m in transcript.messages] == [
        "user", "assistant", "user", "assistant",
    ]
    assert "echoed: hello" in transcript.messages[2].content[0].content  # type: ignore[union-attr]
    transcript.check_invariants()


def test_parallel_calls_in_one_turn() -> None:
    client = ScriptedClient(
        resp(echo_call("c1", "a"), echo_call("c2", "b"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="did both")),
    )
    transcript = run(client)
    results = transcript.messages[2].content
    assert [p.call_id for p in results] == ["c1", "c2"]  # type: ignore[union-attr]


# --- F1: the truncated turn (chapter 3, section 1.2) ---


def test_a_turn_truncated_mid_call_is_discarded() -> None:
    client = ScriptedClient(
        resp(echo_call("c1", "hel"), stop=StopReason.MAX_TOKENS),   # cut off
        resp(TextPart(text="recovered")),
    )
    transcript = run(client)
    # the truncated turn never entered the transcript
    assert [m.role for m in transcript.messages] == ["user", "assistant"]
    assert transcript.messages[-1].content[0].text == "recovered"  # type: ignore[union-attr]
    # and the retry asked for more room
    assert client.requests[1].max_tokens > client.requests[0].max_tokens


def test_a_truncated_text_turn_is_kept() -> None:
    # MAX_TOKENS with NO tool call is a clipped but usable answer — keep it
    transcript = run(
        ScriptedClient(resp(TextPart(text="half an ans"), stop=StopReason.MAX_TOKENS))
    )
    assert len(transcript.messages) == 2


# --- F10: the budgets ---


def test_iteration_cap() -> None:
    forever = [
        resp(echo_call(f"c{i}", "again"), stop=StopReason.TOOL_USE) for i in range(10)
    ]
    with pytest.raises(LoopBudgetExceeded, match="iterations"):
        run(ScriptedClient(*forever), max_iterations=5)


def test_wall_clock_budget() -> None:
    with pytest.raises(LoopBudgetExceeded, match="wall clock"):
        run(ScriptedClient(resp(TextPart(text="hi"))), deadline_seconds=-1.0)


# --- stop reasons ---


def test_unknown_stop_reason_terminates_and_warns() -> None:
    client = ScriptedClient(
        resp(TextPart(text="?"), stop=StopReason.UNKNOWN, raw="something_new")
    )
    transcript = run(client)
    assert len(transcript.messages) == 2     # terminates rather than spinning


def test_a_refusal_terminates_the_loop() -> None:
    transcript = run(
        ScriptedClient(resp(TextPart(text="I cannot help with that."),
                            stop=StopReason.REFUSAL))
    )
    assert len(transcript.messages) == 2


# --- the observer ---


def test_the_observer_sees_every_event_in_order() -> None:
    observer = ListObserver()
    client = ScriptedClient(
        resp(echo_call("c1", "x"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    run(client, observer=observer)
    assert observer.kinds() == [
        "request", "response", "tool_start", "tool_end", "request", "response",
    ]


# --- the request the loop builds ---


def test_the_loop_sends_the_tool_schemas() -> None:
    client = ScriptedClient(resp(TextPart(text="hi")))
    run(client, system="be brief")
    request = client.requests[0]
    assert {tool.name for tool in request.tools} == {"echo"}
    assert request.system == "be brief"


def test_a_provider_error_is_not_swallowed() -> None:
    class Broken:
        name, model = "broken", "m"

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("network on fire")

    with pytest.raises(RuntimeError, match="network on fire"):
        run(Broken())  # type: ignore[arg-type]


# --- the architectural tests ---


def test_the_loop_never_names_a_vendor() -> None:
    import inspect

    import agentloop.loop

    source = inspect.getsource(agentloop.loop).lower()
    for vendor in ("anthropic", "openai", "openrouter", "modal", "httpx", "claude"):
        assert vendor not in source


def test_the_loop_never_names_a_use_case() -> None:
    """The other seam, same trick. `cwd` is the one that would sneak back in."""
    import inspect

    import agentloop.loop

    source = inspect.getsource(agentloop.loop).lower()
    for term in ("cwd", "subprocess", "harness", "file", "shell"):
        assert term not in source


# --- F1 through a real adapter: Rule 1 must not mask the truncation ---


def test_a_truncated_call_from_a_real_adapter_is_discarded() -> None:
    """The fabricated MAX_TOKENS response above bypasses the adapter. This does not."""
    truncated = response_from_wire(
        {
            "model": "m",
            "choices": [{
                "finish_reason": "length",
                "message": {"content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "hel'},
                }]},
            }],
        },
        provider="fake", quirks=Quirks(),
    )
    done = response_from_wire(
        {"model": "m", "choices": [{"finish_reason": "stop",
                                    "message": {"content": "done"}}]},
        provider="fake", quirks=Quirks(),
    )
    client = ScriptedClient(truncated, done)
    transcript = run(client)
    assert not any(
        isinstance(part, ToolCallPart)
        for message in transcript.messages for part in message.content
    )
    assert client.requests[1].max_tokens > client.requests[0].max_tokens


# --- the observer failure policy ---


class ExplodingObserver(ListObserver):
    """A tracer that breaks AFTER the tool has acted."""

    def on_tool_end(self, call: ToolCallPart, result: object) -> None:
        raise RuntimeError("tracer down")


def test_an_observer_that_raises_cannot_leave_a_call_unanswered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = ScriptedClient(
        resp(echo_call("c1", "a"), echo_call("c2", "b"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    with caplog.at_level(logging.ERROR, logger="agentloop.observer"):
        transcript = run(client, observer=ExplodingObserver())
    transcript.check_invariants()
    assert final_text(transcript) == "done"
    assert "tracer down" in caplog.text           # swallowed, never silent


class BrokenResponseObserver(ListObserver):
    def on_response(self, response: ModelResponse) -> None:
        raise RuntimeError("tracer down")


def test_an_observer_that_raises_on_a_response_does_not_end_the_run() -> None:
    transcript = run(ScriptedClient(resp(TextPart(text="done"))),
                     observer=BrokenResponseObserver())
    assert final_text(transcript) == "done"
