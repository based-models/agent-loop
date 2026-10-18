"""OpenTelemetry metrics, as a LoopObserver.

For dashboards and alerts: "what share of runs hit the iteration limit?" is a counter
query, not a trace search. Like `agentloop.tracing`, this module records through the
OpenTelemetry API only, so it produces nothing until the APPLICATION installs a
`MeterProvider` (with `opentelemetry-sdk`, in the `otel` extra). The library never
configures one.

It is driven by each conversation's `ConversationSummary`, so every number here is also
in the summary, and it is stateless: one instance can serve concurrent conversations.

Attributes are deliberately low-cardinality -- status, agent name, provider, model, tool
name. Conversation, correlation and parent ids are NEVER metric attributes: a unique value
per run would give every run its own time series and sink a metrics backend.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Meter

from agentloop.observer import BudgetKind, NullObserver
from agentloop.summary import ConversationSummary


class MetricsObserver(NullObserver):
    """Records conversation outcomes as counters and histograms."""

    def __init__(self, meter: Meter | None = None) -> None:
        meter = meter or metrics.get_meter("agentloop")
        self._conversations = meter.create_counter(
            "agentloop.conversations", unit="{conversation}",
            description="Conversations finished, by status.",
        )
        self._duration = meter.create_histogram(
            "agentloop.conversation.duration", unit="s",
            description="Wall-clock time of a conversation.",
        )
        self._iterations = meter.create_histogram(
            "agentloop.conversation.iterations", unit="{iteration}",
            description="Model calls made by a conversation.",
        )
        self._model_duration = meter.create_histogram(
            "agentloop.conversation.model_duration", unit="s",
            description="Time a conversation spent in model calls, retries included.",
        )
        self._tool_duration = meter.create_histogram(
            "agentloop.conversation.tool_duration", unit="s",
            description="Time a conversation spent in one tool, summed over its calls.",
        )
        self._tool_calls = meter.create_counter(
            "agentloop.tool.calls", unit="{call}",
            description="Tool calls, by tool and whether they errored.",
        )
        self._tokens = meter.create_counter(
            "agentloop.tokens", unit="{token}",
            description="Tokens the provider reported, by type.",
        )
        self._cost = meter.create_counter(
            "agentloop.cost", unit="USD",
            description="Cost the provider reported. Absent when none was reported.",
        )
        self._retries = meter.create_counter(
            "agentloop.provider.retries", unit="{retry}",
            description="Provider attempts beyond the first.",
        )
        self._provider_errors = meter.create_counter(
            "agentloop.provider.errors", unit="{attempt}",
            description="Failed provider attempts, by error type, phase and whether the "
            "request was sent (true, false or unknown).",
        )
        self._stalls = meter.create_counter(
            "agentloop.stalls", unit="{stall}",
            description="Times a tool was repeated with identical arguments to the threshold.",
        )
        self._warnings = meter.create_counter(
            "agentloop.budget_warnings", unit="{warning}",
            description="Configured budget fractions crossed.",
        )

    def on_stall(self, tool_name: str, count: int) -> None:
        self._stalls.add(1, {"tool": tool_name})

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        self._warnings.add(1, {"kind": kind})

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        base: dict[str, Any] = {
            "provider": summary.provider,
            "model": summary.model,
        }
        if summary.agent_name is not None:
            base["agent_name"] = summary.agent_name
        outcome = {**base, "status": str(summary.status)}

        self._conversations.add(1, outcome)
        self._duration.record(summary.elapsed_seconds, outcome)
        self._iterations.record(summary.iterations, outcome)
        self._model_duration.record(summary.model_seconds, base)
        if summary.provider_retries:
            self._retries.add(summary.provider_retries, base)

        for failure in summary.failed_attempts:
            sent = "unknown" if failure.request_sent is None else str(failure.request_sent).lower()
            self._provider_errors.add(
                failure.count,
                {
                    **base,
                    "error_type": failure.error_type,
                    "phase": str(failure.phase) if failure.phase is not None else "unknown",
                    "request_sent": sent,
                },
            )

        for tool, seconds in summary.tool_duration_seconds.items():
            self._tool_duration.record(seconds, {**base, "tool": tool})
        for tool, calls in summary.tool_calls.items():
            errors = summary.tool_errors.get(tool, 0)
            if calls - errors:
                self._tool_calls.add(calls - errors, {"tool": tool, "error": False})
            if errors:
                self._tool_calls.add(errors, {"tool": tool, "error": True})

        usage = summary.usage
        token_types = {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": usage.cache_read_tokens,
            "cache_write": usage.cache_write_tokens,
            "reasoning": usage.reasoning_tokens,
        }
        for kind, count in token_types.items():
            if count:  # None (not reported) and 0 both add nothing
                self._tokens.add(count, {**base, "type": kind})
        if usage.cost_usd is not None:
            self._cost.add(usage.cost_usd, base)
