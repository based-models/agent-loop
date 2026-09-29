"""The agent loop.

Reason -> act -> observe -> repeat, until the model stops asking for tools.
Every line here is a guard against something that happens in practice; chapter
7 part 3 names the evidence for each. Nothing in this module knows what a
provider is.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from agentloop.errors import LoopBudgetExceeded
from agentloop.observer import LoopObserver, NullObserver, guarded
from agentloop.provider import (
    HostedTool,
    LLMClient,
    ModelRequest,
    ModelResponse,
    StopReason,
)
from agentloop.tooling import Registry, ToolDeps, run_tools
from agentloop.transcript import ToolCallPart, Transcript

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 16_000
MAX_TOKENS_CEILING = 64_000
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_DEADLINE_SECONDS = 600.0


def _is_truncated_mid_call(response: ModelResponse) -> bool:
    """A turn cut off while the model was still writing a tool call's arguments.

    Unrecoverable: the arguments are partial, so it cannot be executed, and it
    cannot be skipped either (that leaves an unanswered tool call).
    """
    return response.stop_reason is StopReason.MAX_TOKENS and any(
        isinstance(part, ToolCallPart) for part in response.content
    )


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
    """
    watcher = guarded(observer)
    started = time.monotonic()
    hosted = tuple(hosted_tools)
    continuing = False  # the last assistant message is a paused, open turn

    with watcher.conversation():
        for _ in range(max_iterations):
            if (
                deadline_seconds is not None
                and time.monotonic() - started > deadline_seconds
            ):
                raise LoopBudgetExceeded(
                    f"exceeded {deadline_seconds}s wall clock"
                )

            # ── the one place a steering message would fold in (chapter 16) ──

            request = ModelRequest(
                messages=tuple(transcript.messages),
                system=system,
                tools=registry.schemas(),
                max_tokens=max_tokens,
                hosted_tools=hosted,
            )
            watcher.on_request(request)
            response = client.complete(request)  # retries live BELOW this line
            watcher.on_response(response)

            if _is_truncated_mid_call(response):
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                logger.warning(
                    "turn truncated mid-tool-call; discarding it and retrying "
                    "with max_tokens=%d",
                    max_tokens,
                )
                continue  # F1: do NOT append this turn

            if continuing:
                transcript.extend_last_assistant(response.content, response.origin)
            else:
                transcript.add_assistant_message(response.content, response.origin)

            continuing = response.stop_reason is StopReason.PAUSED
            if continuing:
                logger.debug("provider paused the turn; continuing it")
                continue

            if response.stop_reason is not StopReason.TOOL_USE:
                if response.stop_reason is StopReason.UNKNOWN:
                    logger.warning(
                        "unrecognized stop reason %r; treating as end of turn",
                        response.raw_stop_reason,
                    )
                return transcript

            results = run_tools(response.content, registry, deps, watcher)
            transcript.add_tool_results(results)  # asserts the call/result pairing
        else:
            # Reached only if the loop exhausts max_iterations without ever
            # `return`ing -- still inside `with watcher.conversation()`, so an
            # observer sees this failure the same way it sees the wall-clock one.
            raise LoopBudgetExceeded(f"exceeded {max_iterations} iterations")


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
