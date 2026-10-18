"""What a conversation amounted to, without its content.

A `ConversationSummary` answers "why did this stop, and what did it cost?" from
counts, names and statuses alone. Nothing the model, a tool or a provider wrote
is in here -- not messages, not arguments, not results, not exception text -- so a
summary is safe to log, ship to a metrics backend or persist with a checkpoint
without a redaction step. Content belongs to an observer with `capture_content`.

The loop owns the facts only it knows (iterations, limits, elapsed time, status,
IDs). Everything it merely witnessed is aggregated by `SummaryCollector`, an
ordinary observer, so reporting policy stays out of the loop.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from agentloop.errors import FailurePhase
from agentloop.provider import AttemptRecord, Usage
from agentloop.transcript import Transcript, final_text


class ConversationStatus(StrEnum):
    """How a conversation ended. Branch on this, never on an exception message."""

    COMPLETED = "completed"  # the model stopped asking for tools
    REFUSED = "refused"  # the model declined; the transcript holds its answer
    TRUNCATED = "truncated"  # the final answer hit max_tokens
    ITERATION_LIMIT = "iteration_limit"  # LoopBudgetExceeded: out of iterations
    DEADLINE_EXCEEDED = "deadline_exceeded"  # LoopBudgetExceeded: out of wall clock
    TOKEN_LIMIT = "token_limit"  # LoopBudgetExceeded: a tool call that cannot fit max_tokens
    PROVIDER_ERROR = "provider_error"  # a ProviderError, after any retries
    ERROR = "error"  # anything else raised
    CANCELLED = "cancelled"  # KeyboardInterrupt and friends

    @property
    def succeeded(self) -> bool:
        """True when the conversation returned instead of raising."""
        return self in {
            ConversationStatus.COMPLETED,
            ConversationStatus.REFUSED,
            ConversationStatus.TRUNCATED,
        }

    @property
    def budget_exhausted(self) -> bool:
        return self in {
            ConversationStatus.ITERATION_LIMIT,
            ConversationStatus.DEADLINE_EXCEEDED,
            ConversationStatus.TOKEN_LIMIT,
        }


class SummaryOptions(BaseModel):
    """Opt-in knobs for what a conversation reports. Everything defaults to off.

    One object rather than a keyword per feature, so entry points do not grow a
    parameter each time and an organization can define its settings once.
    """

    model_config = ConfigDict(frozen=True)

    # Record one `TurnRecord` per model call in `ConversationSummary.timeline`.
    # Off by default: the list grows with the number of iterations.
    timeline: bool = False
    # Flag a run when one tool is called with identical arguments this many times
    # (`on_stall`, `summary.stalled`). 3 is a sensible choice. None disables the flag;
    # `max_repeated_tool_calls` is reported either way.
    stall_threshold: int | None = Field(default=None, ge=2)
    # Fractions of the iteration limit and of the deadline at which to call
    # `on_budget_warning`, each at most once per conversation. For example (0.8,).
    warn_at: tuple[float, ...] = ()

    @field_validator("warn_at")
    @classmethod
    def _fractions(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0.0 < fraction <= 1.0 for fraction in value):
            raise ValueError("warn_at fractions must be in (0, 1]")
        return tuple(sorted(set(value)))


class ConversationContext(BaseModel):
    """What is known when a conversation begins; sent to `on_conversation_start`."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    conversation_id: str
    correlation_id: str | None = None
    parent_id: str | None = None
    agent_name: str | None = None
    provider: str
    model: str
    iteration_limit: int | None = None
    deadline_seconds: float | None = None


class FailureCount(BaseModel):
    """How many provider attempts failed in one way. Names, enums and a count only.

    `request_sent` keeps its three states: None is "unknown", not "no".
    """

    model_config = ConfigDict(frozen=True)

    error_type: str
    phase: FailurePhase | None = None
    request_sent: bool | None = None
    count: int = Field(ge=1)


class TurnRecord(BaseModel):
    """One model call and the tools it led to. Content-free, like the summary."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=1)  # 1-based, equal to the iteration number
    model_seconds: float = Field(ge=0)
    # None when the call raised before answering; `error_type` then says why.
    stop_reason: str | None = None
    error_type: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    attempts: int = 1
    # Per-attempt detail; empty when the first attempt succeeded.
    attempt_log: tuple[AttemptRecord, ...] = ()
    # A turn cut off mid tool call: dropped from the transcript and retried.
    discarded: bool = False
    tools: tuple[str, ...] = ()
    tool_errors: tuple[str, ...] = ()
    tool_seconds: float = Field(default=0.0, ge=0)


class ConversationSummary(BaseModel):
    """One conversation, summarized. Frozen, JSON-serializable, content-free.

    `None` means "not known", never zero: `usage.cost_usd` is None unless some
    provider turn reported a cost, and `iteration_limit`/`deadline_seconds` are
    None when the loop was not given one (a deadline of None disables it).
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    conversation_id: str
    # Supplied by the caller to group conversations (one funding-round run, one
    # request). The library only echoes them: grouping is the orchestrator's job.
    correlation_id: str | None = None
    parent_id: str | None = None

    agent_name: str | None = None
    provider: str
    model: str

    status: ConversationStatus
    # Model calls attempted, including a final one that raised, a truncated turn
    # that was discarded and retried, and each paused continuation.
    iterations: int = Field(ge=0)
    iteration_limit: int | None = Field(default=None, ge=0)
    deadline_seconds: float | None = None
    elapsed_seconds: float = Field(ge=0)

    tool_calls: dict[str, int] = Field(default_factory=dict)
    tool_errors: dict[str, int] = Field(default_factory=dict)
    # Extra provider attempts beyond the first, across every model call.
    provider_retries: int = Field(default=0, ge=0)
    usage: Usage = Field(default_factory=Usage)

    # Where the time went. `model_seconds` includes retries and their waits (a subset,
    # `retry_wait_seconds`); `tool_seconds` is wall time spent running tool batches.
    model_seconds: float = Field(default=0.0, ge=0)
    tool_seconds: float = Field(default=0.0, ge=0)
    retry_wait_seconds: float = Field(default=0.0, ge=0)
    # Per tool, the sum of each call's own duration. Tools in one turn run in
    # parallel, so this can exceed `tool_seconds`.
    tool_duration_seconds: dict[str, float] = Field(default_factory=dict)

    # The most times any one tool was called with identical arguments. Counts only:
    # neither the arguments nor a hash of them are ever kept.
    max_repeated_tool_calls: int = Field(default=0, ge=0)
    # True when `SummaryOptions.stall_threshold` was set and reached. A stalled run keeps
    # its own status; this says why a run that hit a limit was probably going nowhere.
    stalled: bool = False

    # None unless `SummaryOptions.timeline` was set.
    timeline: tuple[TurnRecord, ...] | None = None

    # Type name only. The message may quote arguments or code; see the module docstring.
    error_type: str | None = None
    provider_status_code: int | None = None
    # About the call that ended the run, when it was a provider failure. Copied from
    # the error: `error_request_sent` None means the adapter could not tell, and
    # `error_attempts_sent` counts attempts that may have reached the provider (so
    # 0 means nothing was billed). None when no attempt log was recorded.
    error_phase: FailurePhase | None = None
    error_request_sent: bool | None = None
    error_request_id: str | None = None
    error_attempts_sent: int | None = None
    # Every failed provider attempt in the run, retried or not, grouped.
    failed_attempts: tuple[FailureCount, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_hit_rate(self) -> float | None:
        """Share of prompt tokens served from cache; None if the provider reported none."""
        usage = self.usage
        if usage.cache_read_tokens is None:
            return None
        total = usage.input_tokens
        if not usage.input_includes_cached:
            total += usage.cache_read_tokens + (usage.cache_write_tokens or 0)
        return usage.cache_read_tokens / total if total else None

    @property
    def succeeded(self) -> bool:
        return self.status.succeeded


class ConversationResult(BaseModel):
    """What `run_conversation_result` returns: the transcript and how it ended."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    transcript: Transcript
    summary: ConversationSummary

    @property
    def text(self) -> str:
        """The final assistant text. Empty if it said nothing."""
        return final_text(self.transcript)


def new_conversation_id() -> str:
    return uuid.uuid4().hex


def sum_usage(usages: Iterable[Usage]) -> Usage:
    """Add up `usages`, keeping "not reported" distinct from zero.

    An optional field is None only if NO turn reported it; otherwise it is the sum of
    the turns that did. So a cost total can understate a run in which only some
    turns were priced, but it never invents a cost for a provider that reports none.
    """
    usages = list(usages)

    def tokens(values: list[int | None]) -> int | None:
        reported = [value for value in values if value is not None]
        return sum(reported) if reported else None

    costs = [u.cost_usd for u in usages if u.cost_usd is not None]
    return Usage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_read_tokens=tokens([u.cache_read_tokens for u in usages]),
        cache_write_tokens=tokens([u.cache_write_tokens for u in usages]),
        reasoning_tokens=tokens([u.reasoning_tokens for u in usages]),
        cost_usd=sum(costs) if costs else None,
        input_includes_cached=all(u.input_includes_cached for u in usages),
    )
