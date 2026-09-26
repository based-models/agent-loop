"""The Anthropic adapter.

Translation lives in module-level functions so it can be tested without an SDK,
an API key, or a network. The SDK is imported lazily so that installing only
`agentloop[openai-compat]` still leaves this module importable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agentloop.errors import (
    AuthError,
    BadRequestError,
    ContextLengthExceeded,
    ModelNotFound,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    ResponseInvalid,
)
from agentloop.provider import (
    ModelRequest,
    ModelResponse,
    StopReason,
    ToolSchema,
    Usage,
)
from agentloop.providers._common import parse_retry_after
from agentloop.transcript import (
    ContentPart,
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import anthropic

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TIMEOUT_SECONDS = 600.0

_STOP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
    "refusal": StopReason.REFUSAL,
    "pause_turn": StopReason.UNKNOWN,
}

# Arrives as an HTTP 200 but means the request did not fit.
CONTEXT_OVERFLOW_STOP_REASON = "model_context_window_exceeded"

# The same condition as a 400. Like vLLM, message text is all there is to match.
_CONTEXT_MARKERS = ("prompt is too long",)

_TOOL_CHOICE: dict[str, str] = {"auto": "auto", "required": "any", "none": "none"}


def _sdk() -> Any:
    """Import the Anthropic SDK, with an error that names the missing extra."""
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "The Anthropic provider requires the SDK.\n"
            "  pip install 'agentloop[anthropic]'"
        ) from exc
    return anthropic


# --- canonical -> wire -------------------------------------------------------


def tool_to_wire(tool: ToolSchema) -> dict[str, Any]:
    """One tool. `strict` is TOP-LEVEL here; chapter 5 nests it inside `function`."""
    wire: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }
    if tool.strict:
        wire["strict"] = True
    return wire


def _thinking_to_wire(part: ThinkingPart) -> dict[str, Any]:
    """Replay the original block verbatim when we have it."""
    if isinstance(part.raw, dict):
        return part.raw
    block: dict[str, Any] = {"type": "thinking", "thinking": part.text}
    if part.signature is not None:
        block["signature"] = part.signature
    return block


def message_to_wire(message: Message) -> dict[str, Any]:
    """One canonical message -> one Anthropic message. Always one to one."""
    blocks: list[dict[str, Any]] = []
    for part in message.content:
        match part:
            case TextPart():
                blocks.append({"type": "text", "text": part.text})
            case ThinkingPart():
                blocks.append(_thinking_to_wire(part))
            case ToolCallPart():
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": part.id,
                        "name": part.tool_name,
                        "input": part.arguments,
                    }
                )
            case ToolResultPart():
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": part.call_id,
                        "content": part.content,
                        "is_error": part.is_error,
                    }
                )
    return {"role": message.role, "content": blocks}


def request_to_wire(request: ModelRequest, *, model: str) -> dict[str, Any]:
    """Canonical request -> the JSON body the Messages API expects."""
    if request.temperature is not None:
        logger.warning(
            "temperature=%s ignored: the Anthropic SDK no longer accepts sampling "
            "parameters, and current models reject them.",
            request.temperature,
        )

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "messages": [message_to_wire(message) for message in request.messages],
    }
    if request.system:
        payload["system"] = request.system
    if request.tools:
        payload["tools"] = [tool_to_wire(tool) for tool in request.tools]
    if request.stop_sequences:
        payload["stop_sequences"] = list(request.stop_sequences)
    if request.tools and (request.tool_choice != "auto" or not request.parallel_tool_calls):
        # Omitted in the default case, so an ordinary request's body is unchanged.
        choice: dict[str, Any] = {"type": _TOOL_CHOICE[request.tool_choice]}
        if not request.parallel_tool_calls and choice["type"] != "none":
            choice["disable_parallel_tool_use"] = True
        payload["tool_choice"] = choice
    return payload


# --- wire -> canonical -------------------------------------------------------


def usage_from_wire(raw: Any) -> Usage:
    """None means the provider did not report it, which is not the same as zero."""
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", None),
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", None),
    )


def response_from_wire(message: Any) -> ModelResponse:
    """An SDK Message -> the canonical response. Pure: no self, no network."""
    if message.stop_reason == CONTEXT_OVERFLOW_STOP_REASON:
        raise ContextLengthExceeded(
            "input exceeded the model's context window",
            provider=PROVIDER,
            status_code=200,
        )

    parts: list[ContentPart] = []
    for block in message.content:
        match block.type:
            case "text":
                parts.append(TextPart(text=block.text))
            case "thinking" | "redacted_thinking":
                parts.append(
                    ThinkingPart(
                        text=getattr(block, "thinking", "") or "",
                        signature=getattr(block, "signature", None),
                        provider=PROVIDER,
                        model=message.model,
                        raw=block.model_dump(),
                    )
                )
            case "tool_use":
                parts.append(
                    ToolCallPart(
                        id=block.id,
                        tool_name=block.name,
                        arguments=dict(block.input),
                    )
                )
            case _:
                continue

    stop = _STOP.get(message.stop_reason, StopReason.UNKNOWN)
    has_call = any(isinstance(part, ToolCallPart) for part in parts)
    if has_call and stop is not StopReason.MAX_TOKENS:
        stop = StopReason.TOOL_USE  # Rule 1: content wins, unless truncated

    try:
        return ModelResponse(
            content=tuple(parts),
            stop_reason=stop,
            raw_stop_reason=message.stop_reason,
            usage=usage_from_wire(message.usage),
            model=message.model,
            provider=PROVIDER,
            request_id=getattr(message, "id", None),
        )
    except ValidationError as exc:
        raise ResponseInvalid(
            f"Anthropic returned a response we cannot normalize: {exc}",
            provider=PROVIDER,
        ) from exc


# --- errors ------------------------------------------------------------------


def classify_sdk_error(exc: Exception) -> ProviderError:
    """An SDK exception -> the provider-independent subclass.

    The same table as `_openai_compat.classify_http_error`, so an application can
    catch AgentLoopError without knowing which provider it configured.
    """
    sdk = _sdk()
    message = str(exc)
    if isinstance(exc, sdk.APITimeoutError):  # before its parent, APIConnectionError
        return ProviderTimeout(message, provider=PROVIDER)
    if isinstance(exc, sdk.APIConnectionError):
        return ProviderUnavailable(message, provider=PROVIDER)
    if not isinstance(exc, sdk.APIStatusError):
        # A 2xx the SDK could not parse, and anything else it raises.
        return ResponseInvalid(message, provider=PROVIDER)

    status = exc.status_code
    kwargs: dict[str, Any] = {
        "provider": PROVIDER,
        "status_code": status,
        "retry_after": parse_retry_after(exc.response.headers.get("retry-after")),
        "request_id": exc.request_id,
    }
    if status in (401, 403):
        return AuthError(message, **kwargs)
    if status == 404:
        return ModelNotFound(message, **kwargs)
    if status == 408:
        return ProviderTimeout(message, **kwargs)
    if status == 429:
        return RateLimited(message, **kwargs)
    if status == 400:
        if any(marker in message.lower() for marker in _CONTEXT_MARKERS):
            return ContextLengthExceeded(message, **kwargs)
        return BadRequestError(message, **kwargs)
    if status == 409 or status >= 500:  # 409: the SDK's own retry list; 529: overloaded
        return ProviderUnavailable(message, **kwargs)
    return ProviderError(message, **kwargs)


# --- the client --------------------------------------------------------------


class AnthropicClient:
    """An LLMClient backed by the Anthropic Messages API."""

    name = PROVIDER

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        sdk = _sdk()
        self.model = model
        # api_key=None lets the SDK resolve credentials itself:
        # ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN -> an `ant auth login` profile.
        # The annotation is a string at runtime (`from __future__ import annotations`),
        # so it costs nothing even though the SDK is only imported under TYPE_CHECKING.
        # max_retries=0: RetryingClient owns retries for every provider, so they are
        # observable and `max_attempts` means the same thing everywhere.
        self._client: anthropic.Anthropic = sdk.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=0
        )
        self._sdk = sdk

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request_to_wire(request, model=self.model)
        try:
            message = self._client.messages.create(**payload)
        except self._sdk.APIError as exc:
            raise classify_sdk_error(exc) from exc
        return response_from_wire(message)

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        self._client.close()

    def __enter__(self) -> AnthropicClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
