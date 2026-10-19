"""The conversation summary: why a run stopped, delivered however it ended."""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext

import pytest
from pydantic import BaseModel, ConfigDict

from agentloop.agent import Agent
from agentloop.collector import SummaryCollector
from agentloop.errors import (
    AgentLoopError,
    AuthError,
    LoopBudgetExceeded,
    ProviderUnavailable,
    ToolError,
)
from agentloop.loop import run_conversation, run_conversation_result
from agentloop.observer import MultiObserver, NullObserver
from agentloop.provider import ModelRequest, ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.retry import RetryingClient, RetryPolicy
from agentloop.summary import (
    ConversationContext,
    ConversationResult,
    ConversationStatus,
    ConversationSummary,
    SummaryOptions,
    sum_usage,
)
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import (
    ContentPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
)

SECRET = "the-secret-prompt-text"


class ToyDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


def echo(deps: ToyDeps, text: str) -> str:
    return f"echoed: {text}"


def boom(deps: ToyDeps) -> str:
    raise ToolError(f"failed, and quoting {SECRET}")


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


def nap(deps: ToyDeps) -> str:
    time.sleep(0.05)
    return "rested"


REGISTRY = Registry(
    [_spec("echo", echo, text="string"), _spec("boom", boom), _spec("nap", nap)]
)


def resp(
    *parts: ContentPart,
    stop: StopReason = StopReason.END_TURN,
    usage: Usage | None = None,
    attempts: int = 1,
) -> ModelResponse:
    return ModelResponse(
        content=tuple(parts),
        stop_reason=stop,
        usage=usage or Usage(input_tokens=10, output_tokens=2),
        model="fake-1",
        provider="fake",
        attempts=attempts,
    )


def call(call_id: str, tool: str = "echo", **arguments: str) -> ToolCallPart:
    args = arguments if arguments or tool in {"boom", "nap"} else {"text": SECRET}
    return ToolCallPart(id=call_id, tool_name=tool, arguments=args)


def transcript() -> Transcript:
    t = Transcript()
    t.add_user_message(SECRET)
    return t


class EndRecorder(NullObserver):
    def __init__(self) -> None:
        self.ended: list[ConversationSummary] = []

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        self.ended.append(summary)


def converse(client: FakeClient, **kwargs: object) -> ConversationResult:
    return run_conversation_result(
        transcript(), client, REGISTRY, ToyDeps(), **kwargs  # type: ignore[arg-type]
    )


def fails(client: FakeClient, **kwargs: object) -> AgentLoopError:
    with pytest.raises(AgentLoopError) as caught:
        converse(client, **kwargs)
    return caught.value


# --- the outcome, per status ---


def test_a_completed_run_reports_iterations_against_the_limit() -> None:
    client = FakeClient(
        resp(call("c1"), stop=StopReason.TOOL_USE), resp(TextPart(text="done"))
    )
    summary = converse(client, max_iterations=7, agent_name="lookup").summary
    assert summary.status is ConversationStatus.COMPLETED
    assert summary.succeeded
    assert (summary.iterations, summary.iteration_limit) == (2, 7)
    assert summary.agent_name == "lookup"
    assert (summary.provider, summary.model) == ("fake", "fake-1")
    assert summary.elapsed_seconds >= 0
    assert summary.error_type is None


def test_the_result_carries_the_transcript_and_its_text() -> None:
    result = converse(FakeClient(resp(TextPart(text="hello"))))
    assert result.text == "hello"
    assert [m.role for m in result.transcript.messages] == ["user", "assistant"]


def test_a_refusal_is_reported_as_refused() -> None:
    summary = converse(
        FakeClient(resp(TextPart(text="no"), stop=StopReason.REFUSAL))
    ).summary
    assert summary.status is ConversationStatus.REFUSED
    assert summary.succeeded


def test_a_clipped_final_answer_is_reported_as_truncated() -> None:
    summary = converse(
        FakeClient(resp(TextPart(text="half"), stop=StopReason.MAX_TOKENS))
    ).summary
    assert summary.status is ConversationStatus.TRUNCATED


def test_the_iteration_limit_is_a_status_and_structured_on_the_error() -> None:
    client = FakeClient(*[resp(call(f"c{i}"), stop=StopReason.TOOL_USE) for i in range(3)])
    error = fails(client, max_iterations=3)
    assert isinstance(error, LoopBudgetExceeded)
    assert (error.kind, error.limit, error.actual) == ("iterations", 3, 3)
    assert error.summary is not None
    assert error.summary.status is ConversationStatus.ITERATION_LIMIT
    assert error.summary.status.budget_exhausted
    assert not error.summary.succeeded
    assert error.summary.iterations == 3


def test_the_deadline_is_a_different_status_from_the_iteration_limit() -> None:
    error = fails(FakeClient(), deadline_seconds=-1.0)
    assert isinstance(error, LoopBudgetExceeded)
    assert error.kind == "deadline"
    assert error.summary is not None
    assert error.summary.status is ConversationStatus.DEADLINE_EXCEEDED
    assert error.summary.iterations == 0  # it never reached a model call
    assert error.summary.deadline_seconds == -1.0


def test_a_provider_error_reports_its_type_and_status_code() -> None:
    error = fails(FakeClient(AuthError("bad key", status_code=401)))
    assert error.summary is not None
    assert error.summary.status is ConversationStatus.PROVIDER_ERROR
    assert error.summary.error_type == "AuthError"
    assert error.summary.provider_status_code == 401
    assert error.summary.iterations == 1  # the call that raised still counts


def test_an_unexpected_exception_is_reported_and_still_propagates() -> None:
    recorder = EndRecorder()
    with pytest.raises(RuntimeError, match="kaboom"):
        converse(FakeClient(RuntimeError("kaboom")), observer=recorder)
    (summary,) = recorder.ended
    assert summary.status is ConversationStatus.ERROR
    assert summary.error_type == "RuntimeError"


def test_an_interrupt_is_reported_as_cancelled() -> None:
    recorder = EndRecorder()

    class Interrupted(BaseException):
        pass

    class InterruptedClient:
        name, model = "fake", "fake-1"

        def complete(self, request: ModelRequest) -> ModelResponse:
            raise Interrupted

    with pytest.raises(Interrupted):
        run_conversation_result(
            transcript(), InterruptedClient(), REGISTRY, ToyDeps(), observer=recorder
        )
    assert recorder.ended[0].status is ConversationStatus.CANCELLED


# --- delivery ---


def test_the_observer_hears_once_on_success_and_once_on_each_failure() -> None:
    ok = EndRecorder()
    converse(FakeClient(resp(TextPart(text="x"))), observer=ok)
    assert [s.status for s in ok.ended] == [ConversationStatus.COMPLETED]

    bad = EndRecorder()
    with pytest.raises(LoopBudgetExceeded):
        converse(FakeClient(), observer=bad, deadline_seconds=-1.0)
    assert [s.status for s in bad.ended] == [ConversationStatus.DEADLINE_EXCEEDED]


def test_the_observer_sees_the_same_summary_the_caller_gets() -> None:
    recorder = EndRecorder()
    result = converse(FakeClient(resp(TextPart(text="x"))), observer=recorder)
    assert recorder.ended == [result.summary]


def test_the_summary_arrives_inside_conversation() -> None:
    """So a tracing observer can still write to its open span."""
    order: list[str] = []

    class Ordered(NullObserver):
        @contextmanager
        def conversation(self) -> Iterator[None]:
            order.append("enter")
            try:
                yield
            finally:
                order.append("exit")

        def on_conversation_end(self, summary: ConversationSummary) -> None:
            order.append("end")

    converse(FakeClient(resp(TextPart(text="x"))), observer=Ordered())
    assert order == ["enter", "end", "exit"]


def test_a_sink_that_raises_cannot_mask_the_real_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Exploding(NullObserver):
        def on_conversation_end(self, summary: ConversationSummary) -> None:
            raise RuntimeError("sink down")

    with caplog.at_level(logging.ERROR), pytest.raises(AuthError):
        converse(FakeClient(AuthError("bad key")), observer=Exploding())
    assert "on_conversation_end raised" in caplog.text


def test_a_sink_that_raises_does_not_fail_a_good_run() -> None:
    class Exploding(NullObserver):
        def on_conversation_end(self, summary: ConversationSummary) -> None:
            raise RuntimeError("sink down")

    result = converse(FakeClient(resp(TextPart(text="fine"))), observer=Exploding())
    assert result.text == "fine"


def test_an_observer_without_the_hook_is_skipped_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Legacy:  # a pre-existing observer, not a NullObserver subclass
        def conversation(self) -> AbstractContextManager[None]:
            return nullcontext()

        def on_request(self, request: object) -> None: ...
        def on_response(self, response: object) -> None: ...
        def on_retry(self, attempt: int, error: Exception) -> None: ...
        def on_tool_start(self, call: object) -> None: ...
        def on_tool_end(self, call: object, result: object) -> None: ...
        def on_text_delta(self, text: str) -> None: ...

    with caplog.at_level(logging.ERROR):
        converse(FakeClient(resp(TextPart(text="x"))), observer=Legacy())
    assert caplog.text == ""


def test_multi_observer_fans_the_summary_out() -> None:
    first, second = EndRecorder(), EndRecorder()
    converse(FakeClient(resp(TextPart(text="x"))), observer=MultiObserver(first, second))
    assert len(first.ended) == len(second.ended) == 1


def test_run_conversation_still_returns_the_bare_transcript() -> None:
    result = run_conversation(
        transcript(), FakeClient(resp(TextPart(text="x"))), REGISTRY, ToyDeps()
    )
    assert isinstance(result, Transcript)


# --- what was witnessed ---


def test_tool_calls_and_errors_are_counted_by_name() -> None:
    client = FakeClient(
        resp(call("a"), call("b"), call("c", "boom"), stop=StopReason.TOOL_USE),
        resp(call("d"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    summary = converse(client).summary
    assert summary.tool_calls == {"echo": 3, "boom": 1}
    assert summary.tool_errors == {"boom": 1}


def test_usage_sums_across_turns_and_keeps_unreported_cost_as_none() -> None:
    client = FakeClient(
        resp(call("a"), stop=StopReason.TOOL_USE, usage=Usage(input_tokens=5, output_tokens=1)),
        resp(TextPart(text="x"), usage=Usage(input_tokens=7, output_tokens=3)),
    )
    usage = converse(client).summary.usage
    assert (usage.input_tokens, usage.output_tokens) == (12, 4)
    assert usage.cost_usd is None
    assert usage.cache_read_tokens is None


def test_cost_sums_the_turns_that_reported_one() -> None:
    client = FakeClient(
        resp(call("a"), stop=StopReason.TOOL_USE, usage=Usage(cost_usd=0.25)),
        resp(TextPart(text="x"), usage=Usage(cost_usd=0.5, reasoning_tokens=9)),
    )
    usage = converse(client).summary.usage
    assert usage.cost_usd == pytest.approx(0.75)
    assert usage.reasoning_tokens == 9


def test_sum_usage_of_nothing_is_zero_tokens_and_unknown_cost() -> None:
    usage = sum_usage([])
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (0, 0, None)


def test_retries_are_counted_without_wiring_the_observer_into_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    flaky = FakeClient(
        ProviderUnavailable("blip"),
        ProviderUnavailable("blip"),
        resp(TextPart(text="finally")),
    )
    client = RetryingClient(flaky, policy=RetryPolicy(max_attempts=3, base_delay=0.01))
    result = run_conversation_result(transcript(), client, REGISTRY, ToyDeps())
    assert result.summary.provider_retries == 2


def test_retries_before_an_exhausted_call_are_counted_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    flaky = FakeClient(*[ProviderUnavailable("down") for _ in range(3)])
    client = RetryingClient(flaky, policy=RetryPolicy(max_attempts=3, base_delay=0.01))
    with pytest.raises(ProviderUnavailable) as caught:
        run_conversation_result(transcript(), client, REGISTRY, ToyDeps())
    assert caught.value.summary is not None
    assert caught.value.summary.provider_retries == 2
    assert caught.value.summary.status is ConversationStatus.PROVIDER_ERROR


def test_a_first_try_success_reports_no_retries() -> None:
    assert converse(FakeClient(resp(TextPart(text="x")))).summary.provider_retries == 0


def test_a_discarded_truncated_turn_counts_as_an_iteration() -> None:
    client = FakeClient(
        resp(call("c1"), stop=StopReason.MAX_TOKENS), resp(TextPart(text="ok"))
    )
    assert converse(client).summary.iterations == 2


def test_the_collector_is_safe_under_concurrent_tool_calls() -> None:
    collector = SummaryCollector()
    tool_call = call("c")
    ok = ToolResultPart(call_id="c", tool_name="echo", content="y")

    def hammer() -> None:
        for _ in range(500):
            collector.on_tool_end(tool_call, ok)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert collector.tool_calls == {"echo": 4000}


# --- correlation ---


def test_ids_are_generated_and_caller_ids_are_echoed() -> None:
    plain = converse(FakeClient(resp(TextPart(text="x")))).summary
    assert len(plain.conversation_id) == 32
    assert plain.correlation_id is None and plain.parent_id is None

    labelled = converse(
        FakeClient(resp(TextPart(text="x"))),
        conversation_id="conv-1",
        correlation_id="round-42",
        parent_id="lookup-7",
    ).summary
    assert (labelled.conversation_id, labelled.correlation_id, labelled.parent_id) == (
        "conv-1",
        "round-42",
        "lookup-7",
    )


def test_two_conversations_get_different_ids() -> None:
    a = converse(FakeClient(resp(TextPart(text="x")))).summary
    b = converse(FakeClient(resp(TextPart(text="x")))).summary
    assert a.conversation_id != b.conversation_id


def test_agent_run_result_threads_name_and_ids_through() -> None:
    agent = Agent(
        [], client=FakeClient(resp(TextPart(text="hi"))), name="investor-lookup"
    )
    result = agent.run_result("go", correlation_id="round-1", parent_id="p")
    assert result.text == "hi"
    assert result.summary.agent_name == "investor-lookup"
    assert result.summary.correlation_id == "round-1"
    assert result.summary.parent_id == "p"


def test_agent_run_still_returns_text() -> None:
    agent = Agent([], client=FakeClient(resp(TextPart(text="hi"))))
    assert agent.run("go") == "hi"


# --- the content policy ---


def test_no_content_reaches_the_summary_even_when_everything_fails() -> None:
    client = FakeClient(
        resp(call("a"), call("b", "boom"), stop=StopReason.TOOL_USE),
        AuthError(f"rejected {SECRET}", status_code=401),
    )
    error = fails(client)
    assert error.summary is not None
    assert SECRET not in error.summary.model_dump_json()


def test_the_summary_round_trips_through_json() -> None:
    summary = converse(
        FakeClient(resp(call("a"), stop=StopReason.TOOL_USE), resp(TextPart(text="x")))
    ).summary
    assert ConversationSummary.model_validate_json(summary.model_dump_json()) == summary


# --- where the time went ---


class SlowClient(FakeClient):
    """A FakeClient whose model calls take a measurable while."""

    def __init__(self, *responses: ModelResponse | Exception, delay: float = 0.03) -> None:
        super().__init__(*responses)
        self._delay = delay

    def complete(self, request: ModelRequest) -> ModelResponse:
        time.sleep(self._delay)
        return super().complete(request)


def test_time_is_split_between_the_model_and_the_tools() -> None:
    client = SlowClient(
        resp(call("n", "nap"), stop=StopReason.TOOL_USE), resp(TextPart(text="done"))
    )
    summary = converse(client).summary
    assert summary.model_seconds >= 0.06
    assert summary.tool_seconds >= 0.05
    assert summary.tool_duration_seconds["nap"] >= 0.05
    assert summary.elapsed_seconds >= summary.model_seconds + summary.tool_seconds - 0.005


def test_parallel_tools_sum_to_more_than_the_wall_time_they_took() -> None:
    client = FakeClient(
        resp(call("a", "nap"), call("b", "nap"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    summary = converse(client).summary
    assert summary.tool_duration_seconds["nap"] >= 0.1
    assert summary.tool_seconds < summary.tool_duration_seconds["nap"] * 0.9


def test_retry_waits_are_reported_separately_from_model_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    flaky = FakeClient(ProviderUnavailable("blip"), resp(TextPart(text="ok")))
    client = RetryingClient(flaky, policy=RetryPolicy(max_attempts=3, base_delay=2.0))
    summary = run_conversation_result(transcript(), client, REGISTRY, ToyDeps()).summary
    assert summary.retry_wait_seconds >= 2.0  # the backoff it would have slept


def test_the_wait_before_a_call_that_never_answered_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    flaky = FakeClient(*[ProviderUnavailable("down") for _ in range(3)])
    client = RetryingClient(flaky, policy=RetryPolicy(max_attempts=3, base_delay=1.0))
    with pytest.raises(ProviderUnavailable) as caught:
        run_conversation_result(transcript(), client, REGISTRY, ToyDeps())
    assert caught.value.summary is not None
    assert caught.value.summary.retry_wait_seconds >= 3.0  # 1s + 2s of backoff


# --- cache hit rate ---


def hit_rate(usage: Usage) -> float | None:
    return converse(FakeClient(resp(TextPart(text="x"), usage=usage))).summary.cache_hit_rate


def test_the_hit_rate_where_input_tokens_already_include_cached_ones() -> None:
    assert hit_rate(Usage(input_tokens=100, cache_read_tokens=60)) == pytest.approx(0.6)


def test_the_hit_rate_where_cache_reads_are_reported_separately() -> None:
    usage = Usage(
        input_tokens=20, cache_read_tokens=60, cache_write_tokens=20,
        input_includes_cached=False,
    )
    assert hit_rate(usage) == pytest.approx(0.6)  # 60 of 100 prompt tokens


def test_no_hit_rate_when_the_provider_reported_no_cache_tokens() -> None:
    assert hit_rate(Usage(input_tokens=100)) is None
    assert hit_rate(Usage(input_tokens=0, cache_read_tokens=0)) is None


def test_the_hit_rate_is_in_the_json() -> None:
    summary = converse(
        FakeClient(resp(TextPart(text="x"), usage=Usage(input_tokens=10, cache_read_tokens=5)))
    ).summary
    assert '"cache_hit_rate":0.5' in summary.model_dump_json()


# --- stuck loops ---


class SignalRecorder(EndRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.stalls: list[tuple[str, int]] = []
        self.warnings: list[tuple[str, float, float]] = []

    def on_stall(self, tool_name: str, count: int) -> None:
        self.stalls.append((tool_name, count))

    def on_budget_warning(self, kind: str, used: float, limit: float) -> None:
        self.warnings.append((kind, used, limit))


def repeating(times: int) -> FakeClient:
    return FakeClient(
        *[resp(call(f"c{i}"), stop=StopReason.TOOL_USE) for i in range(times)],
        resp(TextPart(text="done")),
    )


def test_a_repeated_call_raises_a_flag_once_at_the_threshold() -> None:
    recorder = SignalRecorder()
    result = converse(
        repeating(4), observer=recorder, summary_options=SummaryOptions(stall_threshold=3)
    )
    assert recorder.stalls == [("echo", 3)]
    assert result.summary.stalled
    assert result.summary.max_repeated_tool_calls == 4
    assert result.summary.status is ConversationStatus.COMPLETED  # the flag is not a status


def test_repeats_are_counted_but_not_flagged_unless_a_threshold_is_set() -> None:
    recorder = SignalRecorder()
    summary = converse(repeating(3), observer=recorder).summary
    assert recorder.stalls == []
    assert not summary.stalled
    assert summary.max_repeated_tool_calls == 3


def test_a_stalled_run_that_hits_its_limit_keeps_the_limit_as_its_status() -> None:
    error = fails(
        repeating(5), max_iterations=4, summary_options=SummaryOptions(stall_threshold=2)
    )
    assert error.summary is not None
    assert error.summary.status is ConversationStatus.ITERATION_LIMIT
    assert error.summary.stalled


def test_distinct_calls_are_not_a_stall() -> None:
    client = FakeClient(
        resp(call("a", text="1"), stop=StopReason.TOOL_USE),
        resp(call("b", text="2"), stop=StopReason.TOOL_USE),
        resp(TextPart(text="done")),
    )
    summary = converse(client, summary_options=SummaryOptions(stall_threshold=2)).summary
    assert not summary.stalled
    assert summary.max_repeated_tool_calls == 1


def test_the_summary_keeps_neither_the_repeated_arguments_nor_a_hash_of_them() -> None:
    summary = converse(
        repeating(3), summary_options=SummaryOptions(stall_threshold=2)
    ).summary
    assert SECRET not in summary.model_dump_json()


# --- budget warnings ---


def test_budget_warnings_are_off_unless_configured() -> None:
    recorder = SignalRecorder()
    converse(repeating(5), observer=recorder, max_iterations=6)
    assert recorder.warnings == []


def test_each_iteration_fraction_warns_once_when_it_is_crossed() -> None:
    recorder = SignalRecorder()
    converse(
        repeating(9),
        observer=recorder,
        max_iterations=10,
        summary_options=SummaryOptions(warn_at=(0.8, 0.5)),
    )
    assert recorder.warnings == [("iterations", 5, 10), ("iterations", 8, 10)]


def test_the_deadline_can_warn_too() -> None:
    recorder = SignalRecorder()
    client = SlowClient(
        resp(call("a"), stop=StopReason.TOOL_USE), resp(TextPart(text="done")), delay=0.12
    )
    converse(
        client,
        observer=recorder,
        deadline_seconds=0.5,
        summary_options=SummaryOptions(warn_at=(0.2,)),
    )
    assert [kind for kind, _, _ in recorder.warnings] == ["deadline"]


def test_no_deadline_means_no_deadline_warnings() -> None:
    recorder = SignalRecorder()
    converse(
        repeating(2),
        observer=recorder,
        deadline_seconds=None,
        max_iterations=10,
        summary_options=SummaryOptions(warn_at=(0.1,)),
    )
    assert {kind for kind, _, _ in recorder.warnings} == {"iterations"}


def test_using_the_whole_iteration_budget_warns_before_the_error() -> None:
    recorder = SignalRecorder()
    with pytest.raises(LoopBudgetExceeded):
        converse(
            repeating(5),
            observer=recorder,
            max_iterations=2,
            summary_options=SummaryOptions(warn_at=(1.0,)),
        )
    assert recorder.warnings == [("iterations", 2, 2)]


@pytest.mark.parametrize("bad", [(0.0,), (1.5,), (-0.1,)])
def test_warn_at_fractions_must_be_in_the_unit_interval(bad: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="warn_at"):
        SummaryOptions(warn_at=bad)


def test_a_stall_threshold_below_two_is_meaningless() -> None:
    with pytest.raises(ValueError):
        SummaryOptions(stall_threshold=1)


# --- the timeline ---


def test_the_timeline_is_off_by_default() -> None:
    assert converse(FakeClient(resp(TextPart(text="x")))).summary.timeline is None


def test_the_timeline_has_one_record_per_model_call() -> None:
    client = FakeClient(
        resp(call("a"), call("b", "boom"), stop=StopReason.TOOL_USE,
             usage=Usage(input_tokens=5, output_tokens=1, cost_usd=0.1)),
        resp(TextPart(text="done"), usage=Usage(input_tokens=9, output_tokens=2)),
    )
    summary = converse(client, summary_options=SummaryOptions(timeline=True)).summary
    assert summary.timeline is not None
    first, second = summary.timeline
    assert (first.index, second.index) == (1, 2)
    assert first.stop_reason == "tool_use"
    assert first.tools == ("echo", "boom")
    assert first.tool_errors == ("boom",)
    assert first.cost_usd == 0.1
    assert first.tool_seconds >= 0
    assert (second.stop_reason, second.tools, second.input_tokens) == ("end_turn", (), 9)
    assert len(summary.timeline) == summary.iterations


def test_a_discarded_truncated_turn_and_a_failed_call_both_appear() -> None:
    client = FakeClient(
        resp(call("a"), stop=StopReason.MAX_TOKENS), AuthError("bad key")
    )
    error = fails(client, summary_options=SummaryOptions(timeline=True))
    assert error.summary is not None and error.summary.timeline is not None
    discarded, failed = error.summary.timeline
    assert discarded.discarded
    assert failed.stop_reason is None
    assert failed.error_type == "AuthError"


def test_the_timeline_is_content_free() -> None:
    summary = converse(
        repeating(2), summary_options=SummaryOptions(timeline=True)
    ).summary
    assert SECRET not in summary.model_dump_json()


# --- the start hook ---


def test_the_observer_is_told_who_is_starting_before_anything_else() -> None:
    seen: list[str] = []

    class Order(EndRecorder):
        def on_conversation_start(self, context: ConversationContext) -> None:
            seen.append(f"start:{context.correlation_id}:{context.iteration_limit}")

        def on_request(self, request: ModelRequest) -> None:
            seen.append("request")

    converse(
        FakeClient(resp(TextPart(text="x"))),
        observer=Order(),
        correlation_id="round-9",
        max_iterations=7,
    )
    assert seen == ["start:round-9:7", "request"]


def test_agent_options_reach_every_run() -> None:
    recorder = SignalRecorder()
    agent = Agent(
        [], client=FakeClient(resp(TextPart(text="x"))), observer=recorder,
        summary_options=SummaryOptions(timeline=True),
    )
    assert agent.run_result("go").summary.timeline is not None


# --- failed provider calls ---


def test_a_failed_call_reports_its_real_attempts_and_whether_it_was_sent() -> None:
    from agentloop.errors import FailurePhase, ProviderUnreachable
    error = ProviderUnreachable(
        "tls", provider="p", phase=FailurePhase.TLS, request_sent=False, request_id="req-9"
    )
    client = RetryingClient(FakeClient(error), policy=RetryPolicy(base_delay=0.01))
    with pytest.raises(ProviderUnreachable) as caught:
        run_conversation_result(
            transcript(), client, REGISTRY, ToyDeps(), summary_options=SummaryOptions(timeline=True)
        )
    summary = caught.value.summary
    assert summary is not None
    assert summary.error_phase is FailurePhase.TLS
    assert summary.error_request_sent is False
    assert summary.error_request_id == "req-9"
    assert summary.error_attempts_sent == 0  # nothing reached the provider
    assert summary.failed_attempts[0].error_type == "ProviderUnreachable"
    assert summary.failed_attempts[0].count == 1
    assert summary.timeline is not None
    assert summary.timeline[0].attempts == 1
    assert summary.timeline[0].attempt_log[0].phase is FailurePhase.TLS
    # Not answered, so not measured: None, never a free-looking 0.
    assert (summary.timeline[0].input_tokens, summary.timeline[0].output_tokens) == (None, None)


def test_a_retried_failure_is_counted_and_the_turn_shows_every_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentloop.errors import FailurePhase, ProviderUnavailable
    monkeypatch.setattr("agentloop.retry.time.sleep", lambda _: None)
    reset = ProviderUnavailable("reset", phase=FailurePhase.READ, request_sent=True)
    client = RetryingClient(
        FakeClient(reset, resp(TextPart(text="ok"))), policy=RetryPolicy(base_delay=0.01)
    )
    summary = run_conversation_result(
        transcript(), client, REGISTRY, ToyDeps(), summary_options=SummaryOptions(timeline=True)
    ).summary
    assert summary.provider_retries == 1
    assert [(f.error_type, f.phase, f.request_sent, f.count) for f in summary.failed_attempts] == [
        ("ProviderUnavailable", FailurePhase.READ, True, 1)
    ]
    assert summary.timeline is not None
    assert summary.timeline[0].attempts == 2
    assert len(summary.timeline[0].attempt_log) == 2
    assert summary.error_attempts_sent is None  # the run succeeded


def test_an_unknown_send_state_stays_unknown_in_the_summary() -> None:
    from agentloop.errors import ProviderUnavailable

    with pytest.raises(ProviderUnavailable) as caught:
        converse(FakeClient(ProviderUnavailable("odd")))
    assert caught.value.summary is not None
    assert caught.value.summary.error_request_sent is None


class ErrorHookObserver(NullObserver):
    def __init__(self) -> None:
        self.errors: list[Exception] = []

    def on_model_error(self, error: Exception) -> None:
        self.errors.append(error)


def test_on_model_error_fires_once_for_a_failed_call() -> None:
    observer = ErrorHookObserver()
    error = ProviderUnavailable("down")
    with pytest.raises(ProviderUnavailable):
        run_conversation_result(
            transcript(), FakeClient(error), REGISTRY, ToyDeps(), observer=observer
        )
    assert observer.errors == [error]


def test_on_model_error_does_not_fire_for_a_successful_run() -> None:
    observer = ErrorHookObserver()
    run_conversation_result(
        transcript(), FakeClient(resp(TextPart(text="x"))), REGISTRY, ToyDeps(),
        observer=observer,
    )
    assert observer.errors == []


def test_an_observer_without_on_model_error_is_skipped_quietly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Legacy:
        def conversation(self) -> AbstractContextManager[None]:
            return nullcontext()

        def on_request(self, request: ModelRequest) -> None: ...
        def on_response(self, response: ModelResponse) -> None: ...
        def on_retry(self, attempt: int, error: Exception) -> None: ...
        def on_tool_start(self, call: ToolCallPart) -> None: ...
        def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: ...
        def on_text_delta(self, text: str) -> None: ...

    with caplog.at_level(logging.WARNING), pytest.raises(ProviderUnavailable):
        run_conversation_result(
            transcript(), FakeClient(ProviderUnavailable("down")), REGISTRY, ToyDeps(),
            observer=Legacy(),  # type: ignore[arg-type]
        )
    assert not caplog.records
