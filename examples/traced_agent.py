#!/usr/bin/env python
"""Chapter 9's datetime agent, with OpenTelemetry turned on.

    docker run -d --name otel -p 4318:4318 -p 16686:16686 \\
        jaegertracing/all-in-one:latest

    set -a; source .env; set +a
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \\
        python -m examples.traced_agent "What time is it in Tokyo and London?"
"""

from __future__ import annotations

import os
import sys

# The tool itself is chapter 10's, unchanged. Importing it rather than copying it
# is the point: tracing is not a property of a tool.
from examples.datetime_agent_terse import get_current_datetime

from agentloop import Agent, ConsoleObserver
from agentloop.tracing import configure_tracing


def main(argv: list[str]) -> int:
    # The environment, read once, by the application. The library never does.
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        configure_tracing(endpoint, service_name="datetime-agent")

    agent = Agent.from_env(
        os.environ,
        tools=[get_current_datetime],
        system="You are precise about time. Call the tool rather than guessing.",
        observer=ConsoleObserver(),
        name="datetime-agent",
        trace=bool(endpoint),
    )
    print(f"tracing to {endpoint or '(nowhere)'}")
    # ConsoleObserver prints the answer; see chapter 10, Part 7.1.
    agent.run(" ".join(argv) or "What time is it in Tokyo and London?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
