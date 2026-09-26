#!/usr/bin/env python
"""The smallest complete agent you can build on agentloop.

One tool, no filesystem, no shell, no application package -- and the same toy
tool Anthropic's notebook opens with (chapter 00), so you can put the two side
by side and see what eight chapters of contract bought.

    set -a; source .env; set +a
    LLM_PROVIDER=anthropic python examples/datetime_agent.py "what time is it in Tokyo?"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict

from agentloop.config import Settings, build_client
from agentloop.errors import AgentLoopError, ToolError
from agentloop.loop import run_conversation
from agentloop.observer import NullObserver
from agentloop.provider import ModelResponse
from agentloop.tooling import ApprovalHook, Registry, ToolSpec, allow_all
from agentloop.transcript import (
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
    to_jsonl,
)

# --- the deps bundle ---------------------------------------------------------
# agentloop's ToolDeps protocol asks for exactly ONE member: the approval hook.
# This agent needs nothing else -- no cwd, no session, no client. That is the
# use-case seam, seen from above for the first time.

class DatetimeDeps(BaseModel):
    model_config = ConfigDict(frozen=True)
    before_tool: ApprovalHook = allow_all


# --- the tool ----------------------------------------------------------------

def get_current_datetime(deps: DatetimeDeps, timezone: str | None) -> str:
    """First parameter is the deps bundle; the rest are the model's arguments."""
    if timezone is None:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S %Z")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        # ToolError is the ONE exception the model is meant to read (ch. 06).
        raise ToolError(f"unknown timezone {timezone!r}; use an IANA name") from exc
    return datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S %Z")


GET_CURRENT_DATETIME = ToolSpec(
    name="get_current_datetime",
    description=(
        "Get the current date and time, optionally in a specific IANA timezone "
        "such as 'Asia/Tokyo'. Pass null for UTC."
    ),
    parameters={
        "type": "object",
        "properties": {
            "timezone": {
                # Chapter 06, part 5, with a runtime consequence for the first
                # time: under strict mode "optional" means "required, may be null".
                "type": ["string", "null"],
                "description": "IANA timezone name, or null for UTC.",
            },
        },
        "required": ["timezone"],
        "additionalProperties": False,
    },
    handler=get_current_datetime,
)

SYSTEM = "You are precise about time. Call the tool rather than guessing."


# --- the UI is an observer ---------------------------------------------------

class PrintObserver(NullObserver):
    """Nine lines, and it is the same protocol a TUI would implement."""

    def on_response(self, response: ModelResponse) -> None:
        for part in response.content:
            if isinstance(part, TextPart) and part.text.strip():
                print(part.text)

    def on_tool_start(self, call: ToolCallPart) -> None:
        print(f"  > {call.tool_name}({call.arguments})")

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        print(f"  {'!' if result.is_error else '+'} {result.content}")

    def on_retry(self, attempt: int, error: Exception) -> None:
        print(f"  ~ retry {attempt}: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="datetime_agent")
    parser.add_argument(
        "prompt", nargs="*", default=["What time is it in Tokyo, and in UTC?"]
    )
    parser.add_argument("--dump", help="Write the transcript as JSONL.")
    args = parser.parse_args(argv)

    # The ONE place this program reads the environment. The library never does:
    # from_env takes the mapping, it does not go and find one. See Part 1.
    settings = Settings.from_env(os.environ)

    observer = PrintObserver()
    client = build_client(settings)
    print(f"{client.name} | {client.model}")

    transcript = Transcript()
    transcript.add_user_message(" ".join(args.prompt))
    try:
        run_conversation(
            transcript,
            client,
            Registry([GET_CURRENT_DATETIME]),
            DatetimeDeps(),
            system=SYSTEM,
            observer=observer,
        )
    except AgentLoopError as error:
        print(f"[{type(error).__name__}] {error}")
        return 1

    transcript.check_invariants()
    if args.dump:
        Path(args.dump).write_text(to_jsonl(transcript.messages))
    print(f"\n{len(transcript.messages)} messages, invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
