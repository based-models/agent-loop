"""Spotting a loop that keeps doing the same thing.

A model stuck on a failing tool tends to call it again with the same arguments. The
tracker counts identical calls so a summary can say "this run was going nowhere"
instead of just "this run ran out of iterations".

Content-free by construction: calls are compared by a hash, and only COUNTS ever leave
this module. Neither the arguments nor the hash are exposed, so nothing a caller could
learn about a call's content reaches a summary, an event or a metric.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from agentloop.transcript import ToolCallPart


def _fingerprint(call: ToolCallPart) -> str:
    # `sort_keys`, so argument order does not make two identical calls look different.
    # An unparseable call has no arguments to compare; its raw text is what repeats.
    payload = (
        call.raw_arguments
        if call.parse_error is not None
        else json.dumps(call.arguments, sort_keys=True, default=str)
    )
    return hashlib.sha256(f"{call.tool_name}\0{payload}".encode()).hexdigest()


class RepeatTracker:
    """Counts identical tool calls across one conversation."""

    def __init__(self, threshold: int | None = None) -> None:
        self._threshold = threshold
        self._counts: dict[str, int] = {}
        self._flagged: set[str] = set()
        self.max_repeats = 0
        self.stalled = False

    def observe(self, calls: Iterable[ToolCallPart]) -> list[tuple[str, int]]:
        """Record `calls`; return `(tool_name, count)` for each that just crossed the threshold.

        Each distinct call is reported at most once, when it first reaches the threshold.
        """
        crossed: list[tuple[str, int]] = []
        for call in calls:
            key = _fingerprint(call)
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            self.max_repeats = max(self.max_repeats, count)
            if (
                self._threshold is not None
                and count >= self._threshold
                and key not in self._flagged
            ):
                self._flagged.add(key)
                self.stalled = True
                crossed.append((call.tool_name, count))
        return crossed
