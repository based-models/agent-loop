#!/usr/bin/env python
"""The same agent as datetime_agent.py, using the ergonomic layer.

    set -a; source .env; set +a
    LLM_PROVIDER=anthropic python examples/datetime_agent_terse.py
"""

import os
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agentloop import Agent, ConsoleObserver, ToolError, tool

DATETIME_SCHEMA = {
    "type": "object",
    "properties": {
        "timezone": {
            "type": ["string", "null"],
            "description": "IANA timezone name, or null for UTC.",
        },
    },
    "required": ["timezone"],
    "additionalProperties": False,
}


@tool(parameters=DATETIME_SCHEMA)
def get_current_datetime(timezone: str | None) -> str:
    """Get the current date and time, optionally in a specific IANA timezone
    such as 'Asia/Tokyo'. Pass null for UTC."""
    if timezone is None:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S %Z")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError(f"unknown timezone {timezone!r}; use an IANA name") from exc
    return datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S %Z")


def main() -> int:
    agent = Agent.from_env(
        os.environ,
        tools=[get_current_datetime],
        system="You are precise about time. Call the tool rather than guessing.",
        observer=ConsoleObserver(),
    )
    # No print(): ConsoleObserver is a live view of the WHOLE conversation, and
    # the final assistant message is part of it. Printing run()'s return value
    # too would print the answer twice. See chapter 10, Part 7.1.
    agent.run(" ".join(sys.argv[1:]) or "What time is it in Tokyo, and in UTC?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
