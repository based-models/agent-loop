"""The Anthropic adapter.

Translation lives in module-level functions so it can be tested without an SDK,
an API key, or a network. The SDK is imported lazily so that installing only
`based-models-agentloop[openai-compat]` still leaves this module importable.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agentloop.errors import (
    AuthError,
    BadRequestError,
    ContextLengthExceeded,
    FailurePhase,
    ModelNotFound,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    ResponseInvalid,
)
from agentloop.provider import (
    HostedTool,
    ModelRequest,
    ModelResponse,
    StopReason,
    ToolSchema,
    Usage,
)
from agentloop.providers._common import (
    parse_retry_after,
    user_location,
    web_search_options,
)
from agentloop.providers._transport import classify_transport_error
from agentloop.transcript import (
    Citation,
    ContentPart,
    HostedToolPart,
    Message,
    Origin,
    Source,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
)

if TYPE_CHECKING:  # pragma: no cover - type checkers only, never imported at runtime
    import anthropic
    import httpx

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"
API = "messages"
HOSTED_TOOLS = frozenset({"web_search"})

# Basic search is the DEFAULT: results reach the model as search-result blocks,
# so it can cite them, and it runs on every model and is ZDR-eligible.
# Dynamic filtering (`dynamic_filtering=True`, Claude 4.6+) has the model run
# code that filters results before they reach its context. The model then reads
# the program's output, not the results, so it returns NO citations -- measured
# live, 0 of 6 requests, versus citations on every basic-search request.
WEB_SEARCH_FILTERED = "web_search_20260209"
WEB_SEARCH_BASIC = "web_search_20250305"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_TIMEOUT_SECONDS = 600.0

_STOP: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
    "refusal": StopReason.REFUSAL,
    "pause_turn": StopReason.PAUSED,
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
            "  pip install 'based-models-agentloop[anthropic]'"
        ) from exc
    return anthropic


# --- canonical -> wire -------------------------------------------------------


def tool_to_wire(tool: ToolSchema) -> dict[str, Any]:
    """One tool. `strict` is TOP-LEVEL here; OpenAI-compatible APIs nest it in `function`."""
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


def hosted_tool_to_wire(tool: HostedTool) -> dict[str, Any]:
    """A hosted web search -> the server tool. Unsupported tuning is logged."""
    options = web_search_options(
        tool, provider=PROVIDER, unsupported=("search_context_size",)
    )
    filtered = options.get("dynamic_filtering", False)
    wire: dict[str, Any] = {
        "type": WEB_SEARCH_FILTERED if filtered else WEB_SEARCH_BASIC,
        "name": "web_search",
    }
    for key in ("allowed_domains", "blocked_domains"):
        if options.get(key):
            wire[key] = list(options[key])
    if options.get("max_uses") is not None:
        wire["max_uses"] = options["max_uses"]
    if options.get("user_location"):
        wire["user_location"] = user_location(options["user_location"])
    return wire


def message_to_wire(message: Message) -> dict[str, Any]:
    """One canonical message -> one Anthropic message. Always one to one.

    An assistant turn this adapter produced goes back EXACTLY as received:
    Anthropic rejects search results or citations whose encrypted fields are
    missing or altered, and dynamic filtering adds blocks the parts do not model.
    """
    if message.origin is not None and message.origin.matches(PROVIDER, API):
        return {"role": message.role, "content": list(message.origin.raw)}
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
    tools = [tool_to_wire(tool) for tool in request.tools]
    tools += [hosted_tool_to_wire(tool) for tool in request.hosted_tools]
    if tools:
        payload["tools"] = tools
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
        input_includes_cached=False,  # Anthropic reports cache reads separately
    )


def _as_dict(value: Any) -> Any:
    """An SDK object, a dict, or a test's SimpleNamespace -> plain JSON-able data.

    `exclude_unset` mirrors how the SDK itself sends a response back: exactly the
    fields the API returned, no more.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_unset=True)
    if isinstance(value, SimpleNamespace):
        return {k: _as_dict(v) for k, v in vars(value).items()}
    if isinstance(value, Mapping):
        return {k: _as_dict(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_as_dict(v) for v in value]
    return value


def _citations(block: Mapping[str, Any]) -> tuple[Citation, ...]:
    return tuple(
        Citation(url=c["url"], title=c.get("title"), cited_text=c.get("cited_text"))
        for c in block.get("citations") or ()
        if isinstance(c, Mapping) and c.get("url")
    )


def _hosted_result(block: Mapping[str, Any]) -> tuple[tuple[Source, ...], str | None]:
    """A `*_tool_result` block's readable part: sources, or an error code.

    Web search: `content` is a list of results, or ONE `*_error` object; nothing
    found is an empty list, not an error. Other tools (dynamic filtering's code
    execution) return a successful result object too -- so only an `error_code`
    or an `*_error` type counts as a failure.
    """
    content = block.get("content")
    if isinstance(content, Mapping):
        kind = str(content.get("type") or "")
        if "error_code" in content or kind.endswith("_error"):
            return (), str(content.get("error_code") or kind)
        return (), None
    sources = tuple(
        Source(url=item["url"], title=item.get("title"))
        for item in content or ()
        if isinstance(item, Mapping) and item.get("url")
    )
    return sources, None


def _parts_from_blocks(
    blocks: list[dict[str, Any]], *, model: str
) -> list[ContentPart]:
    parts: list[ContentPart] = []
    hosted: dict[str, int] = {}  # server_tool_use id -> its index in `parts`
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            parts.append(TextPart(text=block["text"], citations=_citations(block)))
        elif kind in ("thinking", "redacted_thinking"):
            parts.append(
                ThinkingPart(
                    text=block.get("thinking") or "",
                    signature=block.get("signature"),
                    provider=PROVIDER,
                    model=model,
                    raw=block,
                )
            )
        elif kind == "tool_use":
            parts.append(
                ToolCallPart(
                    id=block["id"], tool_name=block["name"], arguments=dict(block["input"])
                )
            )
        elif kind == "server_tool_use":
            query = (block.get("input") or {}).get("query")
            hosted[block["id"]] = len(parts)
            parts.append(
                HostedToolPart(
                    kind=block["name"],
                    call_id=block["id"],
                    query=query if isinstance(query, str) else None,
                )
            )
        elif isinstance(kind, str) and kind.endswith("_tool_result") and "tool_use_id" in block:
            sources, error = _hosted_result(block)
            call_id = block["tool_use_id"]
            if call_id in hosted:
                index = hosted[call_id]
                call = parts[index]
                assert isinstance(call, HostedToolPart)
                parts[index] = call.model_copy(update={"sources": sources, "error": error})
            else:
                # The call was in an earlier response: a mixed client/server turn
                # runs the server tool on the NEXT request.
                parts.append(
                    HostedToolPart(
                        kind=kind.removesuffix("_tool_result"),
                        call_id=call_id,
                        sources=sources,
                        error=error,
                    )
                )
        # Anything else stays only in Origin.raw: preserved for replay, not modelled.
    return parts


def response_from_wire(message: Any) -> ModelResponse:
    """An SDK Message -> the canonical response. Pure: no self, no network."""
    if message.stop_reason == CONTEXT_OVERFLOW_STOP_REASON:
        raise ContextLengthExceeded(
            "input exceeded the model's context window",
            provider=PROVIDER,
            status_code=200,
        )

    blocks = [_as_dict(block) for block in message.content]
    try:
        parts = _parts_from_blocks(blocks, model=message.model)
    except (KeyError, TypeError, ValidationError) as exc:
        raise ResponseInvalid(
            f"Anthropic returned a content block we cannot read: {exc}",
            provider=PROVIDER,
        ) from exc

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
            origin=Origin(provider=PROVIDER, api=API, model=message.model, raw=tuple(blocks)),
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
    if isinstance(exc, sdk.APIConnectionError):  # APITimeoutError is a subclass
        error = classify_transport_error(exc, provider=PROVIDER)
        if isinstance(exc, sdk.APITimeoutError) and not isinstance(error, ProviderTimeout):
            return ProviderTimeout(message, provider=PROVIDER)
        return error
    if not isinstance(exc, sdk.APIStatusError):
        # A 2xx the SDK could not parse, and anything else it raises.
        return ResponseInvalid(
            message, provider=PROVIDER, phase=FailurePhase.BODY, request_sent=True
        )

    status = exc.status_code
    kwargs: dict[str, Any] = {
        "provider": PROVIDER,
        "status_code": status,
        "retry_after": parse_retry_after(exc.response.headers.get("retry-after")),
        "request_id": exc.request_id,
        "phase": FailurePhase.STATUS,
        "request_sent": True,
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
    api = API
    hosted_tools = HOSTED_TOOLS

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        """`http_client` carries trust, proxy and transport settings, as on the
        OpenAI-compatible clients. The SDK follows redirects by default; set
        `follow_redirects=False` on your client if you need a 3xx reported.
        """
        sdk = _sdk()
        self.model = model
        # api_key=None lets the SDK resolve credentials itself:
        # ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN -> an `ant auth login` profile.
        # The annotation is a string at runtime (`from __future__ import annotations`),
        # so it costs nothing even though the SDK is only imported under TYPE_CHECKING.
        # max_retries=0: RetryingClient owns retries for every provider, so they are
        # observable and `max_attempts` means the same thing everywhere.
        self._client: anthropic.Anthropic = sdk.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=0, http_client=http_client
        )
        self._sdk = sdk

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request_to_wire(request, model=self.model)
        try:
            message = self._client.messages.create(**payload)
        except self._sdk.APIError as exc:
            raise classify_sdk_error(exc) from exc
        except TypeError as exc:
            # The SDK defers credential resolution to per-request header
            # validation, which raises a bare TypeError -- not an APIError --
            # when no api_key, auth_token, or CLI profile was ever configured.
            if "Could not resolve authentication method" not in str(exc):
                raise
            raise AuthError(str(exc), provider=self.name) from exc
        return response_from_wire(message)

    def close(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        self._client.close()

    def __enter__(self) -> AnthropicClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
