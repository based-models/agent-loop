"""The OpenAI-compatible adapter, shared by OpenRouter, Modal, and OpenAI.

Two layers. `OpenAICompatClient` is the TRANSPORT: base URL, auth headers,
timeouts, HTTP error classification, the non-JSON guard, `close`. A `Codec` is
the WIRE FORMAT: how a canonical request becomes a body and a body becomes a
canonical response. This module is also the Chat Completions codec
(`/chat/completions`, `api="completions"`), which all three providers speak;
`_openai_responses.py` is the Responses codec (`api="responses"`, OpenAI only).
Translation is module-level functions so it can be tested with a dict and no
network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import httpx

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
    Origin,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
    join_text,
)

API = "completions"
type Api = Literal["completions", "responses"]

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


def _httpx() -> Any:
    """Import httpx, with an error that names the missing extra."""
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "The OpenAI-compatible adapter requires httpx.\n"
            "  pip install 'agentloop[openai-compat]'"
        ) from exc
    return httpx


def httpx_timeout(**kwargs: float) -> httpx.Timeout:
    """Build an `httpx.Timeout` without requiring httpx at import time."""
    return _httpx().Timeout(**kwargs)  # type: ignore[no-any-return]


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
    # OpenAI's own API wants `max_completion_tokens` (required for its reasoning
    # models, recommended for the rest); OpenRouter/Modal still expect `max_tokens`.
    max_tokens_field: str = "max_tokens"


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


def message_to_wire(
    message: Message, quirks: Quirks, provider: str | None = None
) -> list[dict[str, Any]]:
    """One canonical message -> one OR MORE wire messages.

    Tool results fan out: Anthropic's single user message with N result blocks
    becomes N `{"role": "tool"}` messages, in call order.

    An assistant message THIS provider produced also carries back its
    `reasoning_details`, unchanged and in order (OpenRouter: reasoning models
    continue their chain of thought across tool calls). Only that field comes
    from `origin`: the message itself is rebuilt from the parts, because
    `_calls_from_wire` may have rewritten tool-call ids the results refer to.
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
        return [{"role": "user", "content": join_text(message.content)}]

    text = join_text(message.content)
    calls = [p for p in message.content if isinstance(p, ToolCallPart)]

    # `None`, not "": some strict servers reject an empty string beside tool_calls.
    wire: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        wire["tool_calls"] = [_call_to_wire(call) for call in calls]
    origin = message.origin
    if origin is not None and origin.matches(provider, API) and origin.raw:
        details = origin.raw[0].get("reasoning_details")
        if details:
            wire["reasoning_details"] = details
    return [wire]


def request_to_wire(
    request: ModelRequest, *, model: str, quirks: Quirks, provider: str | None = None
) -> dict[str, Any]:
    """Canonical request -> an OpenAI-compatible chat/completions body."""
    if request.hosted_tools:
        # Refused rather than dropped: the caller asked for a capability.
        kinds = sorted({tool.kind for tool in request.hosted_tools})
        raise BadRequestError(
            f"hosted tools {kinds} need the Responses API; Chat Completions has "
            "none (use api='responses')",
            provider=provider,
        )
    messages: list[dict[str, Any]] = []
    if request.system:
        # Exactly one, always first. Many vLLM chat templates raise otherwise.
        messages.append({"role": "system", "content": request.system})
    for message in request.messages:
        messages.extend(message_to_wire(message, quirks, provider))

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        quirks.max_tokens_field: request.max_tokens,
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

    model = body.get("model", "")
    parts: list[ContentPart] = []
    thinking = _reasoning_from_wire(message)
    if thinking is not None:
        parts.append(
            ThinkingPart(text=thinking, provider=provider, model=model or None)
        )
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
        model=model,
        provider=provider,
        request_id=body.get("id"),
        # Kept only when there is something to replay: most turns carry none.
        origin=(
            Origin(provider=provider, api=API, model=model, raw=(dict(message),))
            if message.get("reasoning_details")
            else None
        ),
    )


def _reasoning_from_wire(message: Mapping[str, Any]) -> str | None:
    """OpenRouter's readable reasoning: `reasoning`, else the readable details.

    None when the model reported none. `reasoning.encrypted` details have no
    readable text; they survive only in the origin, for replay.
    """
    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    details = message.get("reasoning_details")
    if not isinstance(details, list) or not details:
        return None
    texts = [
        str(detail.get("text") or detail.get("summary") or "")
        for detail in details
        if isinstance(detail, Mapping)
    ]
    return "\n".join(t for t in texts if t)


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


def _error_for_code(code: int, detail: str, **kwargs: Any) -> ProviderError:
    """Map a status-style code to the right exception subclass.

    Shared by a real HTTP failure (`classify_http_error`) and a 200 whose body
    reports one instead (`classify_error_body`) -- a transient failure reported
    either way should be classified, and retried, the same way.
    """
    if code in (401, 403):
        return AuthError(detail, **kwargs)
    if code == 402:
        return InsufficientCredits(detail, **kwargs)
    if code == 404:
        return ModelNotFound(detail, **kwargs)
    if code == 408:
        return ProviderTimeout(detail, **kwargs)
    if code == 409:
        return ProviderUnavailable(detail, **kwargs)  # a transient conflict, retry
    if code == 429:
        return RateLimited(detail, **kwargs)
    if code == 400:
        if _looks_like_context_overflow(detail):
            return ContextLengthExceeded(detail, **kwargs)
        return BadRequestError(detail, **kwargs)
    if code >= 500:
        return ProviderUnavailable(detail, **kwargs)
    return ProviderError(detail, **kwargs)


# OpenAI sends an empty balance as a 429. Seen live (Sept 2026):
#   {"type": "insufficient_quota", "code": "credit_balance_exhausted"}
_NO_CREDIT_MARKERS = frozenset({"insufficient_quota", "credit_balance_exhausted"})


def _error_markers(response: httpx.Response) -> set[str]:
    """The machine-readable `error.type` and `error.code`, when the body has them."""
    try:
        body = response.json()
    except ValueError:
        return set()
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return set()
    return {str(error[key]) for key in ("type", "code") if error.get(key)}


def classify_http_error(
    response: httpx.Response, *, provider: str, quirks: Quirks
) -> ProviderError:
    """Turn a failed HTTP response into the right exception subclass."""
    status = response.status_code
    if status == 429 and _error_markers(response) & _NO_CREDIT_MARKERS:
        # A 429 that retrying cannot fix: the balance will not refill itself.
        return InsufficientCredits(
            _extract_message(response, quirks), provider=provider, status_code=status
        )
    return _error_for_code(
        status,
        _extract_message(response, quirks),
        provider=provider,
        status_code=status,
        retry_after=parse_retry_after(response.headers.get("retry-after")),
    )


def classify_error_body(error: Any, *, provider: str) -> ProviderError:
    """OpenRouter can report failure inside an HTTP 200 body -- any code the
    body names, not just 429, since the same range of failures can arrive
    either way."""
    if isinstance(error, Mapping):
        message = str(error.get("message") or error)
        code = error.get("code")
    else:
        message, code = str(error), None
    if not isinstance(code, int):
        return ProviderError(message, provider=provider, status_code=200)
    return _error_for_code(code, message, provider=provider, status_code=200)


# --- the codec ---------------------------------------------------------------


@dataclass(frozen=True)
class Codec:
    """One OpenAI wire format: where to POST, and how to translate each way."""

    api: Api
    path: str
    request_to_wire: Callable[..., dict[str, Any]]
    response_from_wire: Callable[..., ModelResponse]
    hosted_tools: frozenset[str]


COMPLETIONS = Codec(
    api="completions",
    path="/chat/completions",
    request_to_wire=request_to_wire,
    response_from_wire=response_from_wire,
    hosted_tools=frozenset(),
)


def codec_for(api: Api) -> Codec:
    if api == "completions":
        return COMPLETIONS
    if api == "responses":
        # Imported here: the Responses codec reuses helpers from this module.
        from agentloop.providers._openai_responses import RESPONSES

        return RESPONSES
    raise ValueError(f"unknown OpenAI api {api!r}; expected 'completions' or 'responses'")


# --- the client --------------------------------------------------------------


class OpenAICompatClient:
    """An LLMClient for an OpenAI-style endpoint: one transport, either codec."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        headers: Mapping[str, str],
        quirks: Quirks,
        timeout: httpx.Timeout,
        api: Api = "completions",
    ) -> None:
        self.name = name
        self.model = model
        self._quirks = quirks
        self._codec = codec_for(api)
        self._httpx = _httpx()
        self._http = self._httpx.Client(
            base_url=base_url.rstrip("/"), headers=dict(headers), timeout=timeout
        )

    @property
    def api(self) -> Api:
        return self._codec.api

    @property
    def hosted_tools(self) -> frozenset[str]:
        return self._codec.hosted_tools

    def complete(self, request: ModelRequest) -> ModelResponse:
        codec = self._codec
        payload = codec.request_to_wire(
            request, model=self.model, quirks=self._quirks, provider=self.name
        )
        try:
            response = self._http.post(codec.path, json=payload)
        except self._httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc), provider=self.name) from exc
        except self._httpx.HTTPError as exc:
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
        return codec.response_from_wire(body, provider=self.name, quirks=self._quirks)

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        self._http.close()

    def __enter__(self) -> OpenAICompatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
