"""Helpers the adapters share. Pure: no SDK, no httpx."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import Any

from agentloop.errors import ConfigurationError
from agentloop.provider import HostedTool

logger = logging.getLogger(__name__)

# Every option a hosted web search may carry, across providers. An adapter
# translates the ones its API has and handles the rest by the policy below.
WEB_SEARCH_OPTIONS = frozenset(
    {
        "allowed_domains",
        "blocked_domains",
        "max_uses",
        "user_location",
        "dynamic_filtering",
        "search_context_size",
    }
)


def parse_retry_after(value: str | None) -> float | None:
    """A `retry-after` header in seconds, or None. HTTP-date values are ignored."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def web_search_options(
    tool: HostedTool, *, provider: str, unsupported: Collection[str] = ()
) -> Mapping[str, Any]:
    """Validate a hosted web search's options for one adapter.

    The policy every adapter shares:
      - an unknown option is a configuration error, never silently dropped;
      - `unsupported` options are TUNING (a limit, a context size): dropping one
        changes cost or quality, not what may be reached, so it is logged;
      - domain lists are RESTRICTIONS: an adapter must translate them or refuse,
        because dropping one would widen what the model can reach.
    """
    if tool.kind != "web_search":
        raise ConfigurationError(
            f"{provider} does not support the hosted tool {tool.kind!r}"
        )
    unknown = set(tool.options) - WEB_SEARCH_OPTIONS
    if unknown:
        raise ConfigurationError(
            f"unknown web_search option(s) {sorted(unknown)}; "
            f"known: {sorted(WEB_SEARCH_OPTIONS)}"
        )
    if tool.options.get("allowed_domains") and tool.options.get("blocked_domains"):
        raise ConfigurationError(
            "web_search takes allowed_domains or blocked_domains, not both"
        )
    options = dict(tool.options)
    for name in unsupported:
        if options.pop(name, None) is not None:
            logger.warning("%s ignores the web_search option %r", provider, name)
    return options


def user_location(value: Mapping[str, Any]) -> dict[str, Any]:
    """Both APIs take the same approximate-location object."""
    return {"type": "approximate", **{k: v for k, v in value.items() if k != "type"}}
