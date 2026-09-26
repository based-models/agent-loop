"""Helpers both adapters share. Pure: no SDK, no httpx."""

from __future__ import annotations


def parse_retry_after(value: str | None) -> float | None:
    """A `retry-after` header in seconds, or None. HTTP-date values are ignored."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
