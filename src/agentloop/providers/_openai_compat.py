"""The OpenAI-compatible adapter, shared by OpenRouter and Modal.

Both speak the same wire protocol; they differ in base URL, auth headers, and
the handful of behaviours captured in `Quirks`. Translation is module-level
functions so it can be tested with a dict and no network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from agentloop.errors import (
    AuthError,
    BadRequestError,
    ContextLengthExceeded,
    InsufficientCredits,
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
    ToolCallPart,
    ToolResultPart,
)

_FINISH: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
    "error": StopReason.ERROR,
    "function_call": StopReason.TOOL_USE,
}

# vLLM reports a context overflow as a plain 400 whose `code` is the INTEGER 400,
# so there is no machine-readable code to match on. Message text is all we get.
_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "max_tokens must be at least 1",
)

MAX_STOP_SEQUENCES = 4          # OpenAI caps `stop` at four entries
ERROR_DETAIL_CHARS = 500


class Quirks(BaseModel):
    """Behavioural differences between OpenAI-compatible servers.

    Each flag is a real observed behaviour, not a hypothetical. See chapter 5,
    part 3 for where each one comes from.
    """

    model_config = ConfigDict(frozen=True)

    supports_strict_tools: bool = True
    reports_usage_details: bool = True
    errors_are_json: bool = True
    supports_parallel_tool_calls_param: bool = True
    default_parallel_tool_calls: bool = True


# --- canonical -> wire -------------------------------------------------------


def tool_to_wire(tool: ToolSchema, quirks: Quirks) -> dict[str, Any]:
    """`strict` goes INSIDE `function` here; chapter 4 puts it top-level."""
    function: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if tool.strict and quirks.supports_strict_tools:
        function["strict"] = True
    return {"type": "function", "function": function}


def _call_to_wire(call: ToolCallPart) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.tool_name,
            # Replay the model's own bytes when we have them, even if unparseable.
            "arguments": call.raw_arguments
            if call.raw_arguments is not None
            else json.dumps(call.arguments),
        },
    }


def message_to_wire(message: Message, quirks: Quirks) -> list[dict[str, Any]]:
    """One canonical message -> one OR MORE wire messages.

    Tool results fan out: Anthropic's single user message with N result blocks
    becomes N `{"role": "tool"}` messages, in call order.
    """
    if message.role == "user":
        results = [p for p in message.content if isinstance(p, ToolResultPart)]
        if results:
            return [
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "name": result.tool_name,
                    "content": result.content,
                }
                for result in results
            ]
        text = "".join(p.text for p in message.content if isinstance(p, TextPart))
        return [{"role": "user", "content": text}]

    text = "".join(p.text for p in message.content if isinstance(p, TextPart))
    calls = [p for p in message.content if isinstance(p, ToolCallPart)]

    # `None`, not "": some strict servers reject an empty string beside tool_calls.
    wire: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        wire["tool_calls"] = [_call_to_wire(call) for call in calls]
    return [wire]


def request_to_wire(
    request: ModelRequest, *, model: str, quirks: Quirks
) -> dict[str, Any]:
    """Canonical request -> an OpenAI-compatible chat/completions body."""
    messages: list[dict[str, Any]] = []
    if request.system:
        # Exactly one, always first. Many vLLM chat templates raise otherwise.
        messages.append({"role": "system", "content": request.system})
    for message in request.messages:
        messages.extend(message_to_wire(message, quirks))

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_tokens,
    }
    if request.tools:
        payload["tools"] = [tool_to_wire(tool, quirks) for tool in request.tools]
        payload["tool_choice"] = request.tool_choice
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.stop_sequences:
        payload["stop"] = list(request.stop_sequences)[:MAX_STOP_SEQUENCES]
    if quirks.supports_parallel_tool_calls_param:
        payload["parallel_tool_calls"] = (
            request.parallel_tool_calls and quirks.default_parallel_tool_calls
        )
    return payload


# --- wire -> canonical -------------------------------------------------------


def parse_arguments(raw: Any) -> tuple[dict[str, Any], str | None, str | None]:
    """Parse tool-call arguments exactly once. Returns (args, raw, error).

    Never raises: a malformed payload still yields a usable ToolCallPart, so the
    call/result pairing invariant survives and the model can be told what broke.
    """
    if isinstance(raw, dict):
        return raw, None, None
    if raw is None:
        return {}, "", None
    if not isinstance(raw, str):
        # A number, list or bool where a JSON string belongs. Keep the bytes the
        # model sent, and let the executor tell it what broke.
        return {}, json.dumps(raw), (
            f"expected a JSON string or object, got {type(raw).__name__}"
        )
    text = raw
    if not text.strip():
        return {}, text, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, text, str(exc)
    if not isinstance(parsed, dict):
        return {}, text, f"expected an object, got {type(parsed).__name__}"
    return parsed, text, None


def usage_from_wire(raw: Mapping[str, Any] | None, quirks: Quirks) -> Usage:
    if not raw:
        return Usage()
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    detailed = quirks.reports_usage_details
    return Usage(
        input_tokens=raw.get("prompt_tokens", 0),
        output_tokens=raw.get("completion_tokens", 0),
        cache_read_tokens=prompt_details.get("cached_tokens") if detailed else None,
        cache_write_tokens=(
            prompt_details.get("cache_write_tokens") if detailed else None
        ),
        reasoning_tokens=(
            completion_details.get("reasoning_tokens") if detailed else None
        ),
        cost_usd=raw.get("cost"),
    )


def _calls_from_wire(raw_calls: list[dict[str, Any]]) -> list[ToolCallPart]:
    """Normalize ids so the loop can assume they are non-empty and unique."""
    parts: list[ToolCallPart] = []
    seen: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        base = str(raw_call.get("id") or f"call_{index}")
        call_id, suffix = base, index
        # Loop, not one suffix: ["x", "x_2", "x"] would otherwise reuse "x_2".
        while call_id in seen:
            call_id = f"{base}_{suffix}"
            suffix += 1
        seen.add(call_id)
        function = raw_call.get("function") or {}
        arguments, raw_arguments, parse_error = parse_arguments(
            function.get("arguments")
        )
        parts.append(
            ToolCallPart(
                id=call_id,
                tool_name=function.get("name") or "unknown",
                arguments=arguments,
                raw_arguments=raw_arguments,
                parse_error=parse_error,
            )
        )
    return parts


def response_from_wire(
    body: Mapping[str, Any], *, provider: str, quirks: Quirks
) -> ModelResponse:
    """A chat/completions body -> the canonical response."""
    # A 200 is not proof of success: OpenRouter reports failures in the body.
    error = body.get("error")
    if error is not None:
        raise classify_error_body(error, provider=provider)

    try:
        return _normalize(body, provider=provider, quirks=quirks)
    except (ValidationError, TypeError, AttributeError, KeyError, ValueError) as exc:
        # A 200 with the wrong shape: non-dict choices, numeric content, ... It is
        # the same condition as an empty turn, so it gets the same (retryable) error.
        raise ResponseInvalid(
            f"{provider} returned a response we cannot normalize: {exc}",
            provider=provider,
        ) from exc


def _normalize(
    body: Mapping[str, Any], *, provider: str, quirks: Quirks
) -> ModelResponse:
    choices = body.get("choices") or []
    if not choices:
        raise ResponseInvalid(
            f"{provider} returned no choices", provider=provider
        )
    choice = choices[0]
    message = choice.get("message") or {}

    parts: list[ContentPart] = []
    text = message.get("content")
    if text:
        parts.append(TextPart(text=text))
    parts.extend(_calls_from_wire(message.get("tool_calls") or []))

    stop = _FINISH.get(choice.get("finish_reason"), StopReason.UNKNOWN)
    has_call = any(isinstance(part, ToolCallPart) for part in parts)
    if has_call and stop is not StopReason.MAX_TOKENS:
        stop = StopReason.TOOL_USE  # Rule 1: content wins, unless truncated

    return ModelResponse(
        content=tuple(parts),
        stop_reason=stop,
        raw_stop_reason=choice.get("finish_reason"),
        usage=usage_from_wire(body.get("usage"), quirks),
        model=body.get("model", ""),
        provider=provider,
        request_id=body.get("id"),
    )


# --- errors ------------------------------------------------------------------


def _extract_message(response: httpx.Response, quirks: Quirks) -> str:
    """Never assume the body is JSON: Modal's proxy 401 is plain text."""
    if not quirks.errors_are_json:
        return response.text[:ERROR_DETAIL_CHARS]
    try:
        body = response.json()
    except ValueError:
        return response.text[:ERROR_DETAIL_CHARS]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:ERROR_DETAIL_CHARS]
    return str(error or body)[:ERROR_DETAIL_CHARS]


def _looks_like_context_overflow(detail: str) -> bool:
    lowered = detail.lower()
    return any(marker in lowered for marker in _CONTEXT_MARKERS)


def classify_http_error(
    response: httpx.Response, *, provider: str, quirks: Quirks
) -> ProviderError:
    """Turn a failed HTTP response into the right exception subclass."""
    status = response.status_code
    detail = _extract_message(response, quirks)
    kwargs: dict[str, Any] = {
        "provider": provider,
        "status_code": status,
        "retry_after": parse_retry_after(response.headers.get("retry-after")),
    }

    if status in (401, 403):
        return AuthError(detail, **kwargs)
    if status == 402:
        return InsufficientCredits(detail, **kwargs)
    if status == 404:
        return ModelNotFound(detail, **kwargs)
    if status == 408:
        return ProviderTimeout(detail, **kwargs)
    if status == 429:
        return RateLimited(detail, **kwargs)
    if status == 400:
        if _looks_like_context_overflow(detail):
            return ContextLengthExceeded(detail, **kwargs)
        return BadRequestError(detail, **kwargs)
    if status >= 500:
        return ProviderUnavailable(detail, **kwargs)
    return ProviderError(detail, **kwargs)


def classify_error_body(error: Any, *, provider: str) -> ProviderError:
    """OpenRouter can report failure inside an HTTP 200 body."""
    if isinstance(error, Mapping):
        message = str(error.get("message") or error)
        code = error.get("code")
    else:
        message, code = str(error), None
    if code == 429:
        return RateLimited(message, provider=provider, status_code=200)
    return ProviderError(message, provider=provider, status_code=200)


# --- the client --------------------------------------------------------------


class OpenAICompatClient:
    """An LLMClient for any OpenAI-compatible chat/completions endpoint."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        headers: Mapping[str, str],
        quirks: Quirks,
        timeout: httpx.Timeout,
    ) -> None:
        self.name = name
        self.model = model
        self._quirks = quirks
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=dict(headers), timeout=timeout
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request_to_wire(request, model=self.model, quirks=self._quirks)
        try:
            response = self._http.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc), provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(str(exc), provider=self.name) from exc

        if response.status_code >= 400:
            raise classify_http_error(
                response, provider=self.name, quirks=self._quirks
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ResponseInvalid(
                f"{self.name} returned a 200 whose body is not JSON: "
                f"{response.text[:ERROR_DETAIL_CHARS]!r}",
                provider=self.name,
            ) from exc
        if not isinstance(body, Mapping):
            raise ResponseInvalid(
                f"{self.name} returned a 200 whose body is not an object",
                provider=self.name,
            )
        return response_from_wire(body, provider=self.name, quirks=self._quirks)

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        self._http.close()

    def __enter__(self) -> OpenAICompatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
