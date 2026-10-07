"""A structured, line-per-event trace of a conversation, for debugging.

`EventTraceObserver` writes one JSON object per event through a callable YOU supply
(`file.write`, a logger, a queue). The library never opens a file: where the trace goes
is the application's decision, and so is whether it may contain content.

By default a line carries only metadata -- names, counts, ids, statuses, sizes -- so a
trace is as safe to keep as a `ConversationSummary`. With `capture_content=True` it also
carries what the model, the tools and the providers actually said, and then EVERY
captured string passes through your `redact` function before it is written. Capturing
without one is refused unless you say so with `allow_unredacted=True`: the library ships
no patterns, because a built-in list of "things that look like secrets" promises more
than it can keep.

One conversation at a time per observer, like `OTelObserver`: the callbacks carry no
conversation id, so two runs sharing an instance would interleave. Build one per
concurrent conversation; it is cheap.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from agentloop.errors import ConfigurationError, ProviderError
from agentloop.observer import BudgetKind, NullObserver
from agentloop.provider import ModelRequest, ModelResponse
from agentloop.summary import ConversationContext, ConversationSummary
from agentloop.transcript import Message, ToolCallPart, ToolResultPart

DEFAULT_MAX_STRING_CHARS = 2000

# The fields of a part that are structure the developer chose (tool names, ids), not
# content the model or a tool produced. Everything else about a part is content.
_STRUCTURAL: dict[str, tuple[str, ...]] = {
    "tool_call": ("id", "tool_name"),
    "tool_result": ("call_id", "tool_name", "is_error"),
}


class EventTraceObserver(NullObserver):
    """Writes one JSON line per event. See the module docstring for the content policy."""

    def __init__(
        self,
        write: Callable[[str], None],
        *,
        capture_content: bool = False,
        redact: Callable[[str], str] | None = None,
        allow_unredacted: bool = False,
        max_string_chars: int | None = DEFAULT_MAX_STRING_CHARS,
        terminator: str = "\n",
    ) -> None:
        """`write` receives each line followed by `terminator` ("" for a logger)."""
        if capture_content and redact is None and not allow_unredacted:
            raise ConfigurationError(
                "capture_content=True needs a `redact` function, so captured text is "
                "scrubbed before it is written. Pass allow_unredacted=True to capture "
                "it as-is."
            )
        self._write = write
        self._capture = capture_content
        self._redact = redact
        self._max = max_string_chars
        self._terminator = terminator
        self._lock = threading.Lock()  # tools run in a pool; seq and write stay ordered
        self._seq = 0
        self._context: ConversationContext | None = None
        self._origin = time.monotonic()
        self._sent_messages = 0  # request messages already written (capture only)
        self._system_sent = False
        self._tool_started: dict[str, float] = {}

    # --- plumbing ---

    def _emit(self, event: str, **fields: Any) -> None:
        with self._lock:
            self._seq += 1
            record: dict[str, Any] = {
                "seq": self._seq,
                "ts": time.time(),
                "t": round(time.monotonic() - self._origin, 6),
                "event": event,
            }
            context = self._context
            if context is not None:
                record["conversation_id"] = context.conversation_id
                if context.correlation_id is not None:
                    record["correlation_id"] = context.correlation_id
                if context.parent_id is not None:
                    record["parent_id"] = context.parent_id
            record.update({k: v for k, v in fields.items() if v is not None})
            self._write(json.dumps(record, default=str, ensure_ascii=False) + self._terminator)

    def _text(self, value: str) -> str:
        """Redact, THEN clip: a secret straddling the cut would otherwise survive it."""
        if self._redact is not None:
            value = self._redact(value)
        if self._max is not None and len(value) > self._max:
            value = f"{value[: self._max]}...[{len(value) - self._max} more chars]"
        return value

    def _scrub(self, tree: Any) -> Any:
        """Redact every string in a JSON-like tree. Keys are schema, not content."""
        if isinstance(tree, str):
            return self._text(tree)
        if isinstance(tree, dict):
            return {key: self._scrub(value) for key, value in tree.items()}
        if isinstance(tree, (list, tuple)):
            return [self._scrub(value) for value in tree]
        return tree

    def _part(self, part: Any) -> dict[str, Any]:
        data = part.model_dump(mode="json")
        kind = data.pop("type", None)
        kept = {key: data.pop(key) for key in _STRUCTURAL.get(kind, ()) if key in data}
        return {"type": kind, **kept, "content": self._scrub(data)}

    def _message(self, message: Message) -> dict[str, Any]:
        return {"role": message.role, "parts": [self._part(p) for p in message.content]}

    # --- lifecycle ---

    def on_conversation_start(self, context: ConversationContext) -> None:
        with self._lock:
            self._context = context
            self._origin = time.monotonic()
            self._seq = 0
            self._sent_messages = 0
            self._system_sent = False
            self._tool_started = {}
        self._emit("conversation_start", context=context.model_dump(mode="json"))

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        self._emit("conversation_end", summary=summary.model_dump(mode="json"))

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        self._emit("budget_warning", kind=kind, used=used, limit=limit)

    def on_stall(self, tool_name: str, count: int) -> None:
        self._emit("stall", tool_name=tool_name, count=count)

    # --- model ---

    def on_request(self, request: ModelRequest) -> None:
        fields: dict[str, Any] = {
            "message_count": len(request.messages),
            "tools": [tool.name for tool in request.tools],
            "hosted_tools": [tool.kind for tool in request.hosted_tools],
            "max_tokens": request.max_tokens,
        }
        if self._capture:
            # Only what is NEW since the last request: the transcript is re-sent whole
            # every turn, and writing it each time would make the trace quadratic.
            with self._lock:
                fresh = request.messages[self._sent_messages :]
                self._sent_messages = len(request.messages)
                send_system = request.system is not None and not self._system_sent
                self._system_sent = self._system_sent or send_system
            fields["new_messages"] = [self._message(m) for m in fresh]
            if send_system and request.system is not None:
                fields["system"] = self._text(request.system)
        self._emit("request", **fields)

    def on_response(self, response: ModelResponse) -> None:
        usage = response.usage
        fields: dict[str, Any] = {
            "stop_reason": str(response.stop_reason),
            "raw_stop_reason": response.raw_stop_reason,
            "model": response.model,
            "provider": response.provider,
            "request_id": response.request_id,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": usage.cost_usd,
            "attempts": response.attempts,
            "retry_wait_seconds": response.retry_wait_seconds or None,
            "tool_calls": [
                part.tool_name for part in response.content if isinstance(part, ToolCallPart)
            ],
        }
        if self._capture:
            fields["content"] = [self._part(part) for part in response.content]
        self._emit("response", **fields)

    def on_retry(self, attempt: int, error: Exception) -> None:
        fields: dict[str, Any] = {
            "attempt": attempt,
            "error_type": type(error).__name__,
            "status_code": error.status_code if isinstance(error, ProviderError) else None,
        }
        if self._capture:
            fields["message"] = self._text(str(error))
        self._emit("retry", **fields)

    # --- tools ---

    def on_tool_start(self, call: ToolCallPart) -> None:
        with self._lock:
            self._tool_started[call.id] = time.monotonic()
        fields: dict[str, Any] = {"tool_name": call.tool_name, "call_id": call.id}
        if self._capture:
            fields["arguments"] = self._scrub(call.arguments)
        self._emit("tool_start", **fields)

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        with self._lock:
            started = self._tool_started.pop(call.id, None)
        fields: dict[str, Any] = {
            "tool_name": call.tool_name,
            "call_id": call.id,
            "is_error": result.is_error,
            "result_bytes": len(result.content.encode("utf-8")),
            "duration_seconds": (
                round(time.monotonic() - started, 6) if started is not None else None
            ),
        }
        if self._capture:
            fields["content"] = self._text(result.content)
        self._emit("tool_end", **fields)
