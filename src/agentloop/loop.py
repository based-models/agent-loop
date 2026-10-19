"""The agent loop.

Reason -> act -> observe -> repeat, until the model stops asking for tools.
Every line here is a guard against something that happens in practice: a
truncated tool call, an unpaired tool result, a runaway loop. Nothing in this module knows what a
provider is.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from agentloop.collector import SummaryCollector
from agentloop.errors import AgentLoopError, LoopBudgetExceeded, ProviderError
from agentloop.observer import BudgetKind, LoopObserver, MultiObserver, NullObserver
from agentloop.provider import (
    AttemptRecord,
    HostedTool,
    LLMClient,
    ModelRequest,
    ModelResponse,
    StopReason,
)
from agentloop.stall import RepeatTracker
from agentloop.summary import (
    ConversationContext,
    ConversationResult,
    ConversationStatus,
    ConversationSummary,
    SummaryOptions,
    TurnRecord,
    new_conversation_id,
)
from agentloop.tooling import Registry, ToolDeps, reported_tool_name, run_tools
from agentloop.transcript import ToolCallPart, ToolResultPart, Transcript

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16_000
MAX_TOKENS_CEILING = 64_000
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_DEADLINE_SECONDS = 600.0


def _is_unusable_truncation(response: ModelResponse) -> bool:
    """A turn cut off before it said anything the loop can keep.

    Either the model was still writing a tool call's arguments (partial, so it cannot
    be executed, and cannot be skipped either: that leaves an unanswered tool call),
    or it produced nothing readable at all (a reasoning model that spent the whole
    budget thinking). A bigger budget is the only thing that can help.
    """
    return response.stop_reason is StopReason.MAX_TOKENS and (
        not response.content
        or any(isinstance(part, ToolCallPart) for part in response.content)
    )


def _status_for(error: BaseException) -> ConversationStatus:
    """How a conversation that raised ended. Reads structured fields, never messages."""
    if isinstance(error, LoopBudgetExceeded):
        if error.kind == "iterations":
            return ConversationStatus.ITERATION_LIMIT
        if error.kind == "deadline":
            return ConversationStatus.DEADLINE_EXCEEDED
        if error.kind == "max_tokens":
            return ConversationStatus.TOKEN_LIMIT
        return ConversationStatus.ERROR  # a budget error raised without saying which
    if isinstance(error, ProviderError):
        return ConversationStatus.PROVIDER_ERROR
    if isinstance(error, Exception):
        return ConversationStatus.ERROR
    return ConversationStatus.CANCELLED  # KeyboardInterrupt, SystemExit, ...


def _status_for_final(response: ModelResponse) -> ConversationStatus:
    if response.stop_reason is StopReason.REFUSAL:
        return ConversationStatus.REFUSED
    if response.stop_reason is StopReason.MAX_TOKENS:
        return ConversationStatus.TRUNCATED
    return ConversationStatus.COMPLETED


def run_conversation(
    transcript: Transcript,
    client: LLMClient,
    registry: Registry,
    deps: ToolDeps,
    *,
    system: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    deadline_seconds: float | None = DEFAULT_DEADLINE_SECONDS,
    observer: LoopObserver | None = None,
    hosted_tools: Sequence[HostedTool] = (),
    agent_name: str | None = None,
    conversation_id: str | None = None,
    correlation_id: str | None = None,
    parent_id: str | None = None,
    summary_options: SummaryOptions | None = None,
) -> Transcript:
    """Run the agent until the model stops asking for tools.

    Mutates and returns `transcript`. Raises LoopBudgetExceeded if the model
    will not stop; propagates ProviderError from the client unchanged.

    `deadline_seconds` is a BETWEEN-TURN budget, checked before each model call.
    A call, its retries, or a tool batch that starts in time runs to completion,
    so a run can overshoot by up to one turn -- and a final answer that lands late
    is still returned, because discarding finished work helps nobody. Bound a
    single call with the client's own timeout.

    `hosted_tools` are declared on every request; the provider runs them. A turn
    the provider pauses (StopReason.PAUSED) is sent back unchanged and continued
    -- without a user message -- and the continuation joins the SAME assistant
    message, because it is one turn. Each continuation counts as an iteration.

    However it ends, `observer.on_conversation_end` receives a `ConversationSummary`
    first. This function discards it on success; use `run_conversation_result` to
    keep it. An AgentLoopError raised out of here carries it as `.summary`.

    `agent_name`, `conversation_id`, `correlation_id` and `parent_id` only label the
    summary. The library generates a conversation id when you give none; the others
    are yours, to group conversations under one run. `summary_options` turns on the
    opt-in extras: a per-turn timeline, stall detection and budget warnings.
    """
    return run_conversation_result(
        transcript,
        client,
        registry,
        deps,
        system=system,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        deadline_seconds=deadline_seconds,
        observer=observer,
        hosted_tools=hosted_tools,
        agent_name=agent_name,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
        parent_id=parent_id,
        summary_options=summary_options,
    ).transcript


def run_conversation_result(
    transcript: Transcript,
    client: LLMClient,
    registry: Registry,
    deps: ToolDeps,
    *,
    system: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    deadline_seconds: float | None = DEFAULT_DEADLINE_SECONDS,
    observer: LoopObserver | None = None,
    hosted_tools: Sequence[HostedTool] = (),
    agent_name: str | None = None,
    conversation_id: str | None = None,
    correlation_id: str | None = None,
    parent_id: str | None = None,
    summary_options: SummaryOptions | None = None,
) -> ConversationResult:
    """`run_conversation`, returning the transcript together with its summary.

    Failure behaves exactly as in `run_conversation`: the exception propagates, and
    an AgentLoopError carries the summary as `.summary`.
    """
    # The collector sits beside the caller's observer rather than inside the loop:
    # the loop supplies what only it knows, and the collector what it witnessed.
    collector = SummaryCollector()
    watcher = MultiObserver(collector, *([observer] if observer is not None else []))
    options = summary_options or SummaryOptions()
    started = time.monotonic()
    cid = conversation_id or new_conversation_id()
    run = _Run(
        transcript=transcript,
        client=client,
        registry=registry,
        deps=deps,
        watcher=watcher,
        system=system,
        max_tokens=max_tokens,
        max_iterations=max_iterations,
        deadline_seconds=deadline_seconds,
        hosted=tuple(hosted_tools),
        started=started,
        options=options,
    )

    def summarize(
        status: ConversationStatus, error: BaseException | None
    ) -> ConversationSummary:
        return ConversationSummary(
            conversation_id=cid,
            correlation_id=correlation_id,
            parent_id=parent_id,
            agent_name=agent_name,
            provider=collector.provider or client.name,
            model=collector.model or client.model,
            status=status,
            iterations=run.iterations,
            iteration_limit=max_iterations,
            deadline_seconds=deadline_seconds,
            elapsed_seconds=time.monotonic() - started,
            model_seconds=run.model_seconds,
            tool_seconds=run.tool_seconds,
            retry_wait_seconds=collector.retry_wait_seconds,
            tool_duration_seconds=collector.tool_duration_seconds,
            max_repeated_tool_calls=run.tracker.max_repeats,
            stalled=run.tracker.stalled,
            timeline=tuple(run.turns) if options.timeline else None,
            tool_calls=collector.tool_calls,
            tool_errors=collector.tool_errors,
            provider_retries=collector.provider_retries,
            usage=collector.usage,
            error_type=type(error).__name__ if error is not None else None,
            provider_status_code=(
                error.status_code if isinstance(error, ProviderError) else None
            ),
            error_phase=error.phase if isinstance(error, ProviderError) else None,
            error_request_sent=(
                error.request_sent if isinstance(error, ProviderError) else None
            ),
            error_request_id=error.request_id if isinstance(error, ProviderError) else None,
            error_attempts_sent=(
                sum(1 for r in error.attempt_log if r.request_sent is not False)
                if isinstance(error, ProviderError) and error.attempt_log
                else None
            ),
            failed_attempts=collector.failed_attempts,
        )

    # Everything runs inside `conversation()`, so the summary reaches observers while
    # their span (if any) is still open, and a failure still reaches them as one.
    with watcher.conversation():
        watcher.on_conversation_start(
            ConversationContext(
                conversation_id=cid,
                correlation_id=correlation_id,
                parent_id=parent_id,
                agent_name=agent_name,
                provider=client.name,
                model=client.model,
                iteration_limit=max_iterations,
                deadline_seconds=deadline_seconds,
            )
        )
        try:
            status = run.drive()
        except BaseException as exc:
            if isinstance(exc, ProviderError):
                # A call that never answered still spent its retries.
                collector.add_failed_attempts(
                    exc.attempts - 1, exc.retry_wait_seconds, exc.attempt_log
                )
            summary = summarize(_status_for(exc), exc)
            if isinstance(exc, AgentLoopError):
                exc.summary = summary
            watcher.on_conversation_end(summary)
            raise
        summary = summarize(status, None)
        watcher.on_conversation_end(summary)
        return ConversationResult(transcript=transcript, summary=summary)


class _Run:
    """One conversation's loop state, so the summary can read it however the loop ends."""

    def __init__(
        self,
        *,
        transcript: Transcript,
        client: LLMClient,
        registry: Registry,
        deps: ToolDeps,
        watcher: LoopObserver,
        system: str | None,
        max_tokens: int,
        max_iterations: int,
        deadline_seconds: float | None,
        hosted: tuple[HostedTool, ...],
        started: float,
        options: SummaryOptions,
    ) -> None:
        self.transcript = transcript
        self.client = client
        self.registry = registry
        self.deps = deps
        self.watcher = watcher
        self.system = system
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.deadline_seconds = deadline_seconds
        self.hosted = hosted
        self.started = started
        self.options = options
        self.iterations = 0  # model calls attempted
        self.model_seconds = 0.0  # inside client.complete, retries and their waits included
        self.tool_seconds = 0.0  # wall time of tool batches
        self.tracker = RepeatTracker(options.stall_threshold)
        self.turns: list[TurnRecord] = []
        self._warned: set[tuple[str, float]] = set()
        self.continuing = False  # the last assistant message is a paused, open turn

    def _warn(self, elapsed: float) -> None:
        """Tell the observer when a configured share of a budget is used. Once each."""
        if not self.options.warn_at:
            return
        usage: list[tuple[BudgetKind, float, float]] = []
        if self.max_iterations > 0:
            usage.append(("iterations", self.iterations, self.max_iterations))
        if self.deadline_seconds is not None and self.deadline_seconds > 0:
            usage.append(("deadline", elapsed, self.deadline_seconds))
        for kind, used, limit in usage:
            for fraction in self.options.warn_at:
                if used >= fraction * limit and (kind, fraction) not in self._warned:
                    self._warned.add((kind, fraction))
                    self.watcher.on_budget_warning(kind, used, limit)

    def _add_turn(
        self,
        model_seconds: float,
        *,
        response: ModelResponse | None = None,
        error: BaseException | None = None,
        discarded: bool = False,
        results: Sequence[ToolResultPart] = (),
        tool_seconds: float = 0.0,
    ) -> None:
        if not self.options.timeline:
            return
        usage = response.usage if response is not None else None
        self.turns.append(
            TurnRecord(
                index=self.iterations,
                model_seconds=model_seconds,
                stop_reason=str(response.stop_reason) if response is not None else None,
                error_type=type(error).__name__ if error is not None else None,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cost_usd=usage.cost_usd if usage else None,
                attempts=(
                    response.attempts
                    if response is not None
                    else error.attempts if isinstance(error, ProviderError) else 1
                ),
                attempt_log=(
                    response.attempt_log
                    if response is not None
                    else error.attempt_log if isinstance(error, ProviderError) else ()
                ),
                discarded=discarded,
                tools=tuple(reported_tool_name(self.registry, r.tool_name) for r in results),
                tool_errors=tuple(
                    reported_tool_name(self.registry, r.tool_name) for r in results if r.is_error
                ),
                tool_seconds=tool_seconds,
            )
        )

    def _resume_pending_calls(self) -> None:
        """Answer tool calls a loaded transcript left unanswered, before any model call.

        `from_jsonl` deliberately loads a log cut off mid-turn (the process died while
        tools ran). Sending that as it stands is a 400 on every provider, which is not
        retried. Running the calls is the natural resume; anything else wrong with the
        transcript is raised here, with the fix, rather than as an opaque provider error.
        """
        pending = self.transcript.pending_tool_calls()
        if pending:
            logger.warning("resuming: running %d tool call(s) left unanswered", len(pending))
            started = time.monotonic()
            results = run_tools(pending, self.registry, self.deps, self.watcher)
            self.tool_seconds += time.monotonic() - started
            self.transcript.add_tool_results(results)
        self.transcript.check_invariants()

    def drive(self) -> ConversationStatus:
        """Loop until the model stops asking for tools; raise if it will not."""
        transcript, watcher = self.transcript, self.watcher
        self._resume_pending_calls()

        for _ in range(self.max_iterations):
            elapsed = time.monotonic() - self.started
            if self.deadline_seconds is not None and elapsed > self.deadline_seconds:
                raise LoopBudgetExceeded(
                    f"exceeded {self.deadline_seconds}s wall clock",
                    kind="deadline",
                    limit=self.deadline_seconds,
                    actual=elapsed,
                )
            self._warn(elapsed)

            # ── the one place a steering message would fold in ──

            request = ModelRequest(
                messages=tuple(transcript.messages),
                system=self.system,
                tools=self.registry.schemas(),
                max_tokens=self.max_tokens,
                hosted_tools=self.hosted,
            )
            watcher.on_request(request)
            self.iterations += 1  # before the call: one that raises still counts
            call_started = time.monotonic()
            try:
                response = self.client.complete(request)  # retries live BELOW this line
            except BaseException as exc:
                spent = time.monotonic() - call_started
                self.model_seconds += spent
                if isinstance(exc, ProviderError) and not exc.attempt_log:
                    # A client without RetryingClient around it: one attempt, which
                    # this error describes, so every consumer sees a log.
                    exc.attempt_log = (AttemptRecord.from_error(exc.attempts, exc),)
                self._add_turn(spent, error=exc)
                if isinstance(exc, Exception):
                    watcher.on_model_error(exc)
                raise
            spent = time.monotonic() - call_started
            self.model_seconds += spent
            watcher.on_response(response)

            if _is_unusable_truncation(response):
                self._add_turn(spent, response=response, discarded=True)
                # Never lower a budget the caller chose above the ceiling.
                grown = max(self.max_tokens, min(self.max_tokens * 2, MAX_TOKENS_CEILING))
                if grown == self.max_tokens:
                    # Retrying the identical request would bill another full turn
                    # for the same cut-off, until the iteration limit.
                    raise LoopBudgetExceeded(
                        f"the model's turn does not fit in max_tokens={self.max_tokens}",
                        kind="max_tokens",
                        limit=self.max_tokens,
                        actual=response.usage.output_tokens,
                    )
                self.max_tokens = grown
                logger.warning(
                    "turn truncated before it was usable; discarding it and retrying "
                    "with max_tokens=%d",
                    self.max_tokens,
                )
                continue  # F1: do NOT append this turn

            if self.continuing:
                transcript.extend_last_assistant(response.content, response.origin)
            else:
                transcript.add_assistant_message(response.content, response.origin)

            self.continuing = response.stop_reason is StopReason.PAUSED
            if self.continuing:
                logger.debug("provider paused the turn; continuing it")
                self._add_turn(spent, response=response)
                continue

            if response.stop_reason is not StopReason.TOOL_USE:
                if response.stop_reason is StopReason.UNKNOWN:
                    logger.warning(
                        "unrecognized stop reason %r; treating as end of turn",
                        response.raw_stop_reason,
                    )
                self._add_turn(spent, response=response)
                return _status_for_final(response)

            calls = [part for part in response.content if isinstance(part, ToolCallPart)]
            for tool_name, count in self.tracker.observe(calls):
                watcher.on_stall(reported_tool_name(self.registry, tool_name), count)

            tools_started = time.monotonic()
            results = run_tools(response.content, self.registry, self.deps, watcher)
            tool_spent = time.monotonic() - tools_started
            self.tool_seconds += tool_spent
            transcript.add_tool_results(results)  # asserts the call/result pairing
            self._add_turn(spent, response=response, results=results, tool_seconds=tool_spent)

        # Reached only if the loop exhausts max_iterations without ever returning --
        # still inside `with watcher.conversation()`, so an observer sees this
        # failure the same way it sees the wall-clock one.
        self._warn(time.monotonic() - self.started)
        raise LoopBudgetExceeded(
            f"exceeded {self.max_iterations} iterations",
            kind="iterations",
            limit=self.max_iterations,
            actual=self.iterations,
        )


class ListObserver(NullObserver):
    """Records every event in order. For tests and debugging."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def on_request(self, request: ModelRequest) -> None:
        self.events.append(("request", request))

    def on_response(self, response: ModelResponse) -> None:
        self.events.append(("response", response))

    def on_retry(self, attempt: int, error: Exception) -> None:
        self.events.append(("retry", error))

    def on_tool_start(self, call: ToolCallPart) -> None:
        self.events.append(("tool_start", call))

    def on_tool_end(self, call: ToolCallPart, result: object) -> None:
        self.events.append(("tool_end", result))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]
