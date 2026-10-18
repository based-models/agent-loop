"""Aggregates what a conversation witnessed, for its summary.

An ordinary `LoopObserver`, deliberately: the loop knows its own facts (iterations,
limits, status) and hands everything else to this. Nothing here knows about
OpenTelemetry, files or any particular sink.

One collector per conversation. The loop builds a fresh one each run, so
concurrent conversations never share counts.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterable

from agentloop.errors import FailurePhase
from agentloop.observer import NullObserver
from agentloop.provider import AttemptRecord, ModelResponse, Usage
from agentloop.summary import FailureCount, sum_usage
from agentloop.transcript import ToolCallPart, ToolResultPart


class SummaryCollector(NullObserver):
    """Counts tool calls and errors, sums usage, and tallies provider retries.

    Thread-safe: tools run in a pool (see tooling.py), so `on_tool_end` can arrive
    from several threads at once.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usages: list[Usage] = []
        self._tool_calls: Counter[str] = Counter()
        self._tool_errors: Counter[str] = Counter()
        self._failures: Counter[tuple[str, FailurePhase | None, bool | None]] = Counter()
        self._retries = 0
        self._retry_wait = 0.0
        self._tool_started: dict[str, float] = {}
        self._tool_seconds: dict[str, float] = {}
        self._model: str | None = None
        self._provider: str | None = None

    def on_response(self, response: ModelResponse) -> None:
        with self._lock:
            self._usages.append(response.usage)
            # RetryingClient stamps the attempt count onto the response, so retries
            # are counted without this collector being wired into the client.
            self._retries += response.attempts - 1
            self._retry_wait += response.retry_wait_seconds
            self._tally(response.attempt_log)
            self._model = response.model
            self._provider = response.provider

    def on_tool_start(self, call: ToolCallPart) -> None:
        with self._lock:
            self._tool_started[call.id] = time.monotonic()

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        with self._lock:
            started = self._tool_started.pop(call.id, None)
            if started is not None:
                took = time.monotonic() - started
                self._tool_seconds[call.tool_name] = (
                    self._tool_seconds.get(call.tool_name, 0.0) + took
                )
            self._tool_calls[call.tool_name] += 1
            if result.is_error:
                self._tool_errors[call.tool_name] += 1

    def add_failed_attempts(
        self, retries: int, waited: float = 0.0, attempt_log: Iterable[AttemptRecord] = ()
    ) -> None:
        """Retries (and the time spent waiting on them) before a call that never answered."""
        with self._lock:
            self._retries += retries
            self._retry_wait += waited
            self._tally(attempt_log)

    def _tally(self, attempt_log: Iterable[AttemptRecord]) -> None:
        # Caller holds the lock.
        for record in attempt_log:
            if not record.ok and record.error_type is not None:
                self._failures[(record.error_type, record.phase, record.request_sent)] += 1

    @property
    def failed_attempts(self) -> tuple[FailureCount, ...]:
        with self._lock:
            return tuple(
                FailureCount(
                    error_type=error_type,
                    phase=phase,
                    request_sent=sent,
                    count=count,
                )
                for (error_type, phase, sent), count in sorted(
                    self._failures.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2]))
                )
            )

    @property
    def usage(self) -> Usage:
        with self._lock:
            return sum_usage(self._usages)

    @property
    def tool_calls(self) -> dict[str, int]:
        with self._lock:
            return dict(self._tool_calls)

    @property
    def tool_errors(self) -> dict[str, int]:
        with self._lock:
            return dict(self._tool_errors)

    @property
    def provider_retries(self) -> int:
        with self._lock:
            return self._retries

    @property
    def retry_wait_seconds(self) -> float:
        with self._lock:
            return self._retry_wait

    @property
    def tool_duration_seconds(self) -> dict[str, float]:
        with self._lock:
            return dict(self._tool_seconds)

    @property
    def model(self) -> str | None:
        """The model that actually answered last; may differ from the client's alias."""
        with self._lock:
            return self._model

    @property
    def provider(self) -> str | None:
        with self._lock:
            return self._provider
