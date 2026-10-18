"""Where the loop reports what it is doing.

A callback protocol rather than an async generator, so the loop stays synchronous and
tracing can attach from outside as OpenTelemetry spans without touching it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import (
    AbstractContextManager,
    ExitStack,
    contextmanager,
    nullcontext,
)
from typing import Literal, Protocol

from agentloop.provider import ModelRequest, ModelResponse
from agentloop.summary import ConversationContext, ConversationSummary
from agentloop.transcript import TextPart, ToolCallPart, ToolResultPart

logger = logging.getLogger(__name__)

type BudgetKind = Literal["iterations", "deadline"]


class LoopObserver(Protocol):
    """Every interesting thing that happens. Implementations may no-op."""

    def conversation(self) -> AbstractContextManager[None]: ...
    def on_request(self, request: ModelRequest) -> None: ...
    def on_response(self, response: ModelResponse) -> None: ...
    def on_retry(self, attempt: int, error: Exception) -> None: ...
    def on_tool_start(self, call: ToolCallPart) -> None: ...
    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None: ...

    # The hooks below are OPTIONAL: an observer that does not define one is simply not
    # told, so observers written before they existed keep working.

    def on_conversation_start(self, context: ConversationContext) -> None:
        """Once, right after `conversation()` is entered: the ids and limits of this run."""
        ...

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        """Once per conversation, on success AND failure, inside `conversation()`."""
        ...

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        """A configured fraction of the iteration limit or deadline has been used."""
        ...

    def on_stall(self, tool_name: str, count: int) -> None:
        """One tool was called with identical arguments `count` times (the threshold)."""
        ...

    def on_model_error(self, error: Exception) -> None:
        """A model call failed for good: not retryable, or retries exhausted.

        Fires once per failed call, before the exception leaves the loop and before
        `on_conversation_end`. Unlike `on_retry`, it covers the final attempt. A
        `ProviderError` carries `phase`, `request_sent` and `attempt_log`.
        """
        ...

    # Reserved for streaming / a TUI. Unused today.
    def on_text_delta(self, text: str) -> None: ...


class NullObserver:
    """Does nothing, so nothing downstream needs a None check."""

    def conversation(self) -> AbstractContextManager[None]:
        return nullcontext()

    def on_request(self, request: ModelRequest) -> None:
        pass

    def on_response(self, response: ModelResponse) -> None:
        pass

    def on_retry(self, attempt: int, error: Exception) -> None:
        pass

    def on_tool_start(self, call: ToolCallPart) -> None:
        pass

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        pass

    def on_conversation_start(self, context: ConversationContext) -> None:
        pass

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        pass

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        pass

    def on_stall(self, tool_name: str, count: int) -> None:
        pass

    def on_model_error(self, error: Exception) -> None:
        pass

    def on_text_delta(self, text: str) -> None:
        pass


class ConsoleObserver(NullObserver):
    """Prints what is happening. The same protocol a TUI implements."""

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


class MultiObserver(NullObserver):
    """Fan every event out to several observers, in order.

    Lives here rather than in the application because `agentloop` owns
    LoopObserver, so `agentloop` is the only party that can be relied on to
    notice when the protocol grows a method.
    """

    def __init__(self, *observers: LoopObserver) -> None:
        # Each child guarded on its own, so one that raises cannot starve the rest.
        self._observers = tuple(guarded(observer) for observer in observers)

    @contextmanager
    def conversation(self) -> Iterator[None]:
        # ExitStack, not nested `with`: the number of children is not known
        # when this is written, and they must unwind in reverse even on error.
        with ExitStack() as stack:
            for observer in self._observers:
                stack.enter_context(observer.conversation())
            yield

    def on_request(self, request: ModelRequest) -> None:
        for observer in self._observers:
            observer.on_request(request)

    def on_response(self, response: ModelResponse) -> None:
        for observer in self._observers:
            observer.on_response(response)

    def on_retry(self, attempt: int, error: Exception) -> None:
        for observer in self._observers:
            observer.on_retry(attempt, error)

    def on_tool_start(self, call: ToolCallPart) -> None:
        for observer in self._observers:
            observer.on_tool_start(call)

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        for observer in self._observers:
            observer.on_tool_end(call, result)

    def on_conversation_start(self, context: ConversationContext) -> None:
        for observer in self._observers:
            observer.on_conversation_start(context)

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        for observer in self._observers:
            observer.on_conversation_end(summary)

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        for observer in self._observers:
            observer.on_budget_warning(kind, used, limit)

    def on_stall(self, tool_name: str, count: int) -> None:
        for observer in self._observers:
            observer.on_stall(tool_name, count)

    def on_model_error(self, error: Exception) -> None:
        for observer in self._observers:
            observer.on_model_error(error)

    def on_text_delta(self, text: str) -> None:
        for observer in self._observers:
            observer.on_text_delta(text)


class GuardedObserver:
    """The failure policy: telemetry never becomes an execution failure.

    An `on_*` callback that raises is logged with its traceback and swallowed. A
    tracer that breaks after a tool has already acted must not stop that tool's
    result reaching the transcript -- the loop would be left with an unanswered call.

    `conversation()` is NOT guarded. It runs before any work happens, so failing
    there costs nothing, and it is where an observer reports misuse (see
    OTelObserver's one-run-at-a-time rule).
    """

    def __init__(self, inner: LoopObserver) -> None:
        self._inner = inner

    def conversation(self) -> AbstractContextManager[None]:
        return self._inner.conversation()

    def _safely(self, event: str, *args: object) -> None:
        try:
            getattr(self._inner, event)(*args)
        except Exception:  # noqa: BLE001 - that is the policy
            logger.exception(
                "observer %s.%s raised; ignoring it", type(self._inner).__name__, event
            )

    def on_request(self, request: ModelRequest) -> None:
        self._safely("on_request", request)

    def on_response(self, response: ModelResponse) -> None:
        self._safely("on_response", response)

    def on_retry(self, attempt: int, error: Exception) -> None:
        self._safely("on_retry", attempt, error)

    def on_tool_start(self, call: ToolCallPart) -> None:
        self._safely("on_tool_start", call)

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        self._safely("on_tool_end", call, result)

    def _optionally(self, event: str, *args: object) -> None:
        # An optional hook: an observer predating it is skipped, not logged as broken.
        if hasattr(self._inner, event):
            self._safely(event, *args)

    def on_conversation_start(self, context: ConversationContext) -> None:
        self._optionally("on_conversation_start", context)

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        self._optionally("on_conversation_end", summary)

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        self._optionally("on_budget_warning", kind, used, limit)

    def on_stall(self, tool_name: str, count: int) -> None:
        self._optionally("on_stall", tool_name, count)

    def on_model_error(self, error: Exception) -> None:
        self._optionally("on_model_error", error)

    def on_text_delta(self, text: str) -> None:
        self._safely("on_text_delta", text)


def guarded(observer: LoopObserver | None) -> LoopObserver:
    """`observer` under the failure policy. None becomes a NullObserver.

    Idempotent, so every entry point (loop, run_tools, RetryingClient) can apply it
    without caring whether a caller above already did.
    """
    if observer is None:
        return NullObserver()
    # `type is`, not isinstance: ListObserver and friends subclass NullObserver.
    if isinstance(observer, GuardedObserver) or type(observer) is NullObserver:
        return observer
    return GuardedObserver(observer)
