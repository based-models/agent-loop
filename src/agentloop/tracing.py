"""OpenTelemetry spans, as a LoopObserver.

`loop.py` never imports OpenTelemetry: tracing attaches from outside, which is
the payoff of the observer callback protocol.

Attribute names follow the GenAI semantic conventions
(open-telemetry/semantic-conventions-genai, docs/gen-ai/gen-ai-spans.md).
Status: Development — these names will change; pin what you targeted.

This module never touches global state on its own: nothing here runs at import,
and it never reads OTEL_* variables. `configure_tracing` DOES install a global
provider -- but only when you call it, with an endpoint you chose. See Part 3.2.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode

from agentloop.errors import ConfigurationError, ProviderError
from agentloop.observer import BudgetKind
from agentloop.provider import ModelRequest, ModelResponse
from agentloop.summary import ConversationContext, ConversationSummary
from agentloop.transcript import HostedToolPart, ToolCallPart, ToolResultPart

if TYPE_CHECKING:  # pragma: no cover - the SDK is the [otel] extra
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger(__name__)

_tracer = trace.get_tracer("agentloop")

DEFAULT_AGENT_NAME = "agentloop"
ERROR_DETAIL_CHARS = 200


def _failure_attributes(error: ProviderError, *, prefix: str) -> dict[str, Any]:
    """Phase and send state as span attributes; None (unknown) is omitted, never False."""
    attributes: dict[str, Any] = {}
    if error.phase is not None:
        attributes[f"{prefix}.phase"] = str(error.phase)
    if error.request_sent is not None:
        attributes[f"{prefix}.request_sent"] = error.request_sent
    return attributes


class OTelObserver:
    """Emits one root span per conversation, a child per model request, and a
    sibling per tool call. Retries become events on the model span.

    ONE CONVERSATION AT A TIME. The observer holds the in-flight model span, and
    retries arrive with nothing to say which request they belong to, so two runs
    sharing it would corrupt each other's spans. A second concurrent
    `conversation()` raises instead. Concurrent runs need one observer -- one
    traced Agent -- each.

    With `capture_content` off, nothing the model, a tool or a provider wrote
    reaches a span: that includes error messages, which quote arguments and code.

    Turning it on needs a `redact` function (or an explicit `allow_unredacted=True`),
    the same rule as `EventTraceObserver`. Every captured string goes through `redact`
    first, and error details are clipped to `ERROR_DETAIL_CHARS` after it. A `redact`
    that raises drops that one attribute rather than writing it unredacted.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        agent_name: str = DEFAULT_AGENT_NAME,
        capture_content: bool = False,
        redact: Callable[[str], str] | None = None,
        allow_unredacted: bool = False,
    ) -> None:
        if capture_content and redact is None and not allow_unredacted:
            raise ConfigurationError(
                "capture_content=True needs a `redact` function, so captured text is "
                "scrubbed before it is exported. Pass allow_unredacted=True to capture "
                "it as-is."
            )
        self._provider = provider
        self._model = model
        # The APPLICATION's name, because it is what a trace reader is looking for.
        self._agent_name = agent_name
        # Off by default: a coding agent's prompts contain the user's source code.
        self._capture = capture_content
        self._redact = redact
        self._root: Span | None = None
        self._model_span: Span | None = None
        self._tool_spans: dict[str, Span] = {}
        # Tools run in a thread pool (see tooling.py): on_tool_start/on_tool_end/
        # _end_unfinished all touch _tool_spans concurrently, so it needs its own
        # lock -- a separate concern from `_running`, which guards conversations.
        self._tool_spans_lock = threading.Lock()
        # The root span's context, passed EXPLICITLY to every child: tools run on
        # worker threads, where the ambient context is empty.
        self._context: Context | None = None
        self._running = threading.Lock()

    def _text(self, value: str, limit: int | None = None) -> str | None:
        """`value` redacted, then clipped; None when it must not be written.

        The caller drops the attribute on None. Redact comes first so a secret cut in
        half by the clip is never exported.
        """
        if self._redact is not None:
            try:
                value = self._redact(value)
            except Exception:
                logger.exception("redact raised; dropping the attribute")
                return None
        return value if limit is None else value[:limit]

    def _set_text(self, span: Span, key: str, value: str) -> None:
        text = self._text(value)
        if text is not None:
            span.set_attribute(key, text)

    def _error_detail(self, exc: BaseException) -> str | None:
        return self._text(str(exc), ERROR_DETAIL_CHARS) if self._capture else None

    def _record_exception(self, span: Span, exc: BaseException) -> None:
        """An `exception` event with a redacted message and NO stack trace: the SDK's
        own `record_exception` writes both unredacted."""
        attributes: dict[str, Any] = {"exception.type": type(exc).__name__}
        detail = self._error_detail(exc)
        if detail is not None:
            attributes["exception.message"] = detail
        span.add_event("exception", attributes)

    @contextmanager
    def conversation(self) -> Iterator[None]:
        if not self._running.acquire(blocking=False):
            raise RuntimeError(
                "this OTelObserver is already tracing a conversation; concurrent "
                "runs need one observer (one traced Agent) each"
            )
        try:
            with _tracer.start_as_current_span(
                f"invoke_agent {self._agent_name}",
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.provider.name": self._provider,
                    "gen_ai.agent.name": self._agent_name,
                },
                # Both default to writing the exception MESSAGE onto the span,
                # which may quote content; the except below does it by policy.
                record_exception=False,
                set_status_on_exception=False,
            ) as root:
                self._root = root
                self._context = trace.set_span_in_context(root)
                try:
                    yield
                except BaseException as exc:
                    # This cleanup must never replace `exc`: it is the loop's real
                    # failure (a provider error, a budget exceeded, a tool crash),
                    # not the "already tracing" misuse error above -- a telemetry
                    # bug here should be logged, not swap out what the caller sees.
                    try:
                        root.set_attribute("error.type", type(exc).__name__)
                        if self._capture:
                            self._record_exception(root, exc)
                        root.set_status(Status(StatusCode.ERROR, self._error_detail(exc)))
                        self._end_unfinished(exc)
                    except Exception:
                        logger.exception(
                            "tracing cleanup failed while handling %r", exc
                        )
                    raise
        finally:
            self._root = None
            self._model_span = None
            self._tool_spans = {}
            self._context = None
            self._running.release()

    def _end_unfinished(self, exc: BaseException) -> None:
        """End whatever the exception interrupted -- usually a provider call that
        raised, so `on_response` never came -- as an error."""
        with self._tool_spans_lock:
            spans = list(self._tool_spans.values())
        if self._model_span is not None:
            spans.append(self._model_span)
        for span in spans:
            try:
                span.set_attribute("error.type", type(exc).__name__)
                if self._capture:
                    self._record_exception(span, exc)
                span.set_status(Status(StatusCode.ERROR))
            finally:
                span.end()

    # --- the model request ---

    def on_request(self, request: ModelRequest) -> None:
        span = _tracer.start_span(
            f"chat {self._model}",
            context=self._context,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": self._provider,  # NOT gen_ai.system
                "gen_ai.request.model": self._model,
                "gen_ai.request.max_tokens": request.max_tokens,
                "agentloop.request.message_count": len(request.messages),
                "agentloop.request.tool_count": len(request.tools),
            },
        )
        if request.temperature is not None:
            span.set_attribute("gen_ai.request.temperature", request.temperature)
        if self._capture and request.system:
            self._set_text(span, "gen_ai.system_instructions", request.system)
        self._model_span = span

    def on_response(self, response: ModelResponse) -> None:
        span = self._model_span
        if span is None:
            return
        usage = response.usage
        span.set_attribute("gen_ai.response.model", response.model)
        span.set_attribute(
            "gen_ai.response.finish_reasons", [str(response.stop_reason)]
        )
        span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)

        # None means "not reported": omit rather than assert a zero that was
        # never measured.
        if usage.cache_read_tokens is not None:
            span.set_attribute(
                "gen_ai.usage.cache_read.input_tokens", usage.cache_read_tokens
            )
        if usage.cache_write_tokens is not None:
            span.set_attribute(
                "gen_ai.usage.cache_write.input_tokens", usage.cache_write_tokens
            )
        if usage.reasoning_tokens is not None:
            span.set_attribute(
                "gen_ai.usage.reasoning.output_tokens", usage.reasoning_tokens
            )
        # There is no gen_ai.* attribute for cost, so it gets our own prefix.
        if usage.cost_usd is not None:
            span.set_attribute("agentloop.usage.cost_usd", usage.cost_usd)
        if response.raw_stop_reason:
            span.set_attribute(
                "agentloop.response.raw_stop_reason", response.raw_stop_reason
            )
        span.set_attribute("agentloop.attempts", response.attempts)
        if response.request_id:
            span.set_attribute("gen_ai.response.id", response.request_id)
        # A count, never the queries: those are content (see capture_content).
        hosted = sum(isinstance(part, HostedToolPart) for part in response.content)
        if hosted:
            span.set_attribute("agentloop.hosted_tool.calls", hosted)
        span.end()
        self._model_span = None

    def on_retry(self, attempt: int, error: Exception) -> None:
        """An event on the model span, not a span: it happened DURING the request."""
        if self._model_span is None:
            return
        attributes: dict[str, Any] = {
            "agentloop.retry.attempt": attempt,
            "agentloop.retry.error_type": type(error).__name__,
        }
        if isinstance(error, ProviderError):
            if error.status_code is not None:
                attributes["http.response.status_code"] = error.status_code
            attributes.update(_failure_attributes(error, prefix="agentloop.retry"))
        if self._capture:
            # A provider's 400 can quote the request back, so this is content.
            detail = self._error_detail(error)
            if detail is not None:
                attributes["agentloop.retry.message"] = detail
        self._model_span.add_event("retry", attributes)

    def on_model_error(self, error: Exception) -> None:
        """Describe the failed call on its span, which is still open: `error.type` and
        the status are added when the exception reaches the root span's handler."""
        span = self._model_span
        if span is None or not isinstance(error, ProviderError):
            return
        span.set_attribute("agentloop.attempts", error.attempts)
        span.set_attributes(_failure_attributes(error, prefix="agentloop.error"))

    # --- tools ---

    def on_tool_start(self, call: ToolCallPart) -> None:
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": call.tool_name,
            "gen_ai.tool.call.id": call.id,
            "gen_ai.tool.type": "function",
        }
        if self._capture:
            # Imported here, not at module scope: this is the only use, it is
            # unreachable with capture off, and the import allowlist in
            # tests/test_layering.py stays shorter for it.
            import json

            arguments = self._text(json.dumps(call.arguments))
            if arguments is not None:
                attributes["gen_ai.tool.call.arguments"] = arguments
        span = _tracer.start_span(
            f"execute_tool {call.tool_name}", context=self._context, attributes=attributes
        )
        # Keyed by call id: tools run in parallel.
        with self._tool_spans_lock:
            self._tool_spans[call.id] = span

    def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
        with self._tool_spans_lock:
            span = self._tool_spans.pop(call.id, None)
        if span is None:
            return
        try:
            span.set_attribute("agentloop.tool.is_error", result.is_error)
            span.set_attribute(
                "agentloop.tool.result_bytes", len(result.content.encode("utf-8"))
            )
            if self._capture:
                self._set_text(span, "gen_ai.tool.call.result", result.content)
            if result.is_error:
                # The error text quotes arguments and file contents: content, not metadata.
                detail = (
                    self._text(result.content, ERROR_DETAIL_CHARS) if self._capture else None
                )
                span.set_status(Status(StatusCode.ERROR, detail))
        finally:
            span.end()

    def on_conversation_start(self, context: ConversationContext) -> None:
        """Stamp the ids on the root span immediately, so even a run that never
        finishes (a crashed process, an in-flight span) can be found by them."""
        root = self._root
        if root is None:
            return
        root.set_attribute("agentloop.conversation.id", context.conversation_id)
        if context.correlation_id is not None:
            root.set_attribute("agentloop.conversation.correlation_id", context.correlation_id)
        if context.parent_id is not None:
            root.set_attribute("agentloop.conversation.parent_id", context.parent_id)

    def on_budget_warning(self, kind: BudgetKind, used: float, limit: float) -> None:
        if self._root is not None:
            self._root.add_event(
                "agentloop.budget_warning",
                {"kind": kind, "used": used, "limit": limit},
            )

    def on_stall(self, tool_name: str, count: int) -> None:
        if self._root is not None:
            self._root.add_event(
                "agentloop.stall", {"tool": tool_name, "count": count}
            )

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        """Put the outcome on the root span, where a trace reader looks first.

        Counts, names and statuses only, so it is the same with capture on or off.
        Optional fields that are None are omitted rather than asserted as zero.
        """
        root = self._root
        if root is None:
            return
        attributes: dict[str, Any] = {
            "agentloop.conversation.id": summary.conversation_id,
            "agentloop.conversation.status": str(summary.status),
            "agentloop.conversation.iterations": summary.iterations,
            "agentloop.conversation.elapsed_seconds": summary.elapsed_seconds,
            "agentloop.conversation.provider_retries": summary.provider_retries,
            "agentloop.conversation.model_seconds": summary.model_seconds,
            "agentloop.conversation.tool_seconds": summary.tool_seconds,
            "agentloop.conversation.retry_wait_seconds": summary.retry_wait_seconds,
            "agentloop.conversation.max_repeated_tool_calls": summary.max_repeated_tool_calls,
            "agentloop.conversation.stalled": summary.stalled,
            "agentloop.usage.input_tokens": summary.usage.input_tokens,
            "agentloop.usage.output_tokens": summary.usage.output_tokens,
        }
        optional: dict[str, Any] = {
            "agentloop.conversation.correlation_id": summary.correlation_id,
            "agentloop.conversation.parent_id": summary.parent_id,
            "agentloop.conversation.iteration_limit": summary.iteration_limit,
            "agentloop.conversation.deadline_seconds": summary.deadline_seconds,
            "agentloop.usage.cost_usd": summary.usage.cost_usd,
            "agentloop.usage.cache_hit_rate": summary.cache_hit_rate,
            "http.response.status_code": summary.provider_status_code,
            "agentloop.error.type": summary.error_type,
            "agentloop.error.phase": (
                str(summary.error_phase) if summary.error_phase is not None else None
            ),
            "agentloop.error.request_sent": summary.error_request_sent,
            "agentloop.error.attempts_sent": summary.error_attempts_sent,
            "agentloop.error.request_id": summary.error_request_id,
        }
        attributes.update({k: v for k, v in optional.items() if v is not None})
        # One flat attribute per tool: OTel attribute values cannot be maps.
        for name, count in summary.tool_calls.items():
            attributes[f"agentloop.tool.calls.{name}"] = count
        for name, count in summary.tool_errors.items():
            attributes[f"agentloop.tool.errors.{name}"] = count
        root.set_attributes(attributes)

    def on_text_delta(self, text: str) -> None:
        """Reserved for streaming."""


# --- setup: opt-in, and the only function here that touches global state -----


def configure_tracing(endpoint: str, *, service_name: str = "agentloop") -> TracerProvider:
    """Install a process-global tracer provider exporting to `endpoint`.

    Returns the provider so the caller owns its lifecycle: `force_flush()` before a
    short-lived process exits, `shutdown()` when done. Spans are batched, so
    without one of those the last few may never leave.

    Opt-in and explicit. YOU call this, with an endpoint YOU chose -- the library
    never calls it, never runs it at import, and never goes looking for
    OTEL_EXPORTER_OTLP_ENDPOINT itself. Same shape as Settings.from_env(env).

    The SDK imports are inside the function because the SDK lives in the [otel]
    extra. That is what keeps module scope API-only, so `import agentloop.tracing`
    works on a bare install and simply produces no spans.
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise ImportError(
            "Tracing needs the OpenTelemetry SDK, which is an optional extra. "
            "Install: pip install 'based-models-agentloop[otel]'"
        ) from exc

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    return provider
