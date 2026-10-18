"""The OpenAI Responses codec (`POST /responses`, `api="responses"`).

The transport -- auth, timeouts, HTTP errors, `close` -- is `OpenAICompatClient`'s;
this module only translates. Two decisions shape it:

STATELESS, ALWAYS. Responses can keep the conversation on OpenAI's side
(`store`, which DEFAULTS TO TRUE, plus `previous_response_id` or a Conversation).
agentloop's Transcript is the single source of truth -- JSONL resume, a discarded
truncated turn, provider switching, blind retries -- and a second, server-side
history would drift from it. So every request sends `store: false` and the full
transcript as `input`, exactly as Chat Completions and Anthropic already do.

REPLAY FROM THE ORIGIN. Reasoning models need their `reasoning` items (with
`encrypted_content`, which `store: false` returns by default) and each message's
`phase` sent back, in order. The canonical parts cannot rebuild those, so an
assistant turn this codec produced is replayed from `Message.origin.raw`, with
item ids removed: under `store: false` an id makes the server look up an item it
never persisted, and the request fails.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from agentloop.errors import (
    FailurePhase,
    ProviderError,
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
from agentloop.providers._common import user_location, web_search_options
from agentloop.providers._openai_compat import Codec, Quirks, parse_arguments
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

logger = logging.getLogger(__name__)

API = "responses"
HOSTED_TOOLS = frozenset({"web_search"})

# Replayed without an id: it points at server-side storage that `store: false`
# never created, and the lookup fails ("Item with id 'rs_…' not found").
_OUTPUT_ONLY_FIELDS = frozenset({"id"})

# Hosted-tool CALL items are not replayed. The evidence they produced is already
# in the message's `url_citation` annotations, and an id-less call item is not a
# valid input. Reasoning and messages -- what the model needs -- are replayed.
_NOT_REPLAYED = frozenset({"web_search_call"})

_INCOMPLETE = {
    "max_output_tokens": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}

# `failed` responses carry an error code string, not an HTTP status.
_RETRYABLE_CODES = {"rate_limit_exceeded": RateLimited, "server_error": ProviderUnavailable}


# --- canonical -> wire -------------------------------------------------------


def tool_to_wire(tool: ToolSchema, quirks: Quirks) -> dict[str, Any]:
    """Flat, unlike Chat Completions' `{"type": "function", "function": {...}}`."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "strict": tool.strict and quirks.supports_strict_tools,
    }


def hosted_tool_to_wire(tool: HostedTool, *, provider: str) -> dict[str, Any]:
    """A hosted web search -> the `web_search` tool. Anthropic-only tuning is logged."""
    options = web_search_options(
        tool, provider=provider, unsupported=("max_uses", "dynamic_filtering")
    )
    wire: dict[str, Any] = {"type": "web_search"}
    filters = {
        key: list(options[key])
        for key in ("allowed_domains", "blocked_domains")
        if options.get(key)
    }
    if filters:
        wire["filters"] = filters
    if options.get("user_location"):
        wire["user_location"] = user_location(options["user_location"])
    if options.get("search_context_size"):
        wire["search_context_size"] = options["search_context_size"]
    return wire


def _replayable(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in _OUTPUT_ONLY_FIELDS}


def _call_to_wire(call: ToolCallPart) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.id,
        "name": call.tool_name,
        "arguments": call.raw_arguments
        if call.raw_arguments is not None
        else json.dumps(call.arguments),
    }


def message_to_items(message: Message, provider: str | None = None) -> list[dict[str, Any]]:
    """One canonical message -> Responses input items, in order."""
    if message.role == "user":
        items: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, ToolResultPart):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": part.call_id,
                        "output": part.content,
                    }
                )
            elif isinstance(part, TextPart):
                items.append(
                    {"role": "user", "content": [{"type": "input_text", "text": part.text}]}
                )
        return items

    origin = message.origin
    if origin is not None and origin.matches(provider, API):
        return [
            _replayable(item) for item in origin.raw if item.get("type") not in _NOT_REPLAYED
        ]

    # Produced elsewhere (Chat Completions, Anthropic, by hand): rebuild what is
    # portable. Reasoning and hosted-tool activity cannot cross providers.
    items = []
    for part in message.content:
        if isinstance(part, TextPart) and part.text:
            items.append({"role": "assistant", "content": part.text})
        elif isinstance(part, ToolCallPart):
            items.append(_call_to_wire(part))
    return items


def request_to_wire(
    request: ModelRequest, *, model: str, quirks: Quirks, provider: str | None = None
) -> dict[str, Any]:
    """Canonical request -> a stateless Responses body."""
    items: list[dict[str, Any]] = []
    for message in request.messages:
        items.extend(message_to_items(message, provider))

    payload: dict[str, Any] = {
        "model": model,
        "input": items,
        "store": False,
        "max_output_tokens": request.max_tokens,
    }
    if request.system:
        payload["instructions"] = request.system
    tools = [tool_to_wire(tool, quirks) for tool in request.tools]
    tools += [
        hosted_tool_to_wire(tool, provider=provider or "openai") for tool in request.hosted_tools
    ]
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = request.tool_choice
        if quirks.supports_parallel_tool_calls_param:
            payload["parallel_tool_calls"] = (
                request.parallel_tool_calls and quirks.default_parallel_tool_calls
            )
    if any(tool.kind == "web_search" for tool in request.hosted_tools):
        # Without this the search call carries its query but not the pages it read.
        payload["include"] = ["web_search_call.action.sources"]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.stop_sequences:
        logger.warning(
            "stop_sequences ignored: the Responses API has no stop parameter"
        )
    return payload


# --- wire -> canonical -------------------------------------------------------


def usage_from_wire(raw: Mapping[str, Any] | None, quirks: Quirks) -> Usage:
    if not raw:
        return Usage()
    detailed = quirks.reports_usage_details
    input_details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    return Usage(
        input_tokens=raw.get("input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        cache_read_tokens=input_details.get("cached_tokens") if detailed else None,
        reasoning_tokens=output_details.get("reasoning_tokens") if detailed else None,
    )


def _citations(annotations: Any) -> tuple[Citation, ...]:
    return tuple(
        Citation(
            url=a["url"],
            title=a.get("title"),
            start=a.get("start_index"),
            end=a.get("end_index"),
        )
        for a in annotations or ()
        if isinstance(a, Mapping) and a.get("type") == "url_citation" and a.get("url")
    )


def _search_part(item: Mapping[str, Any]) -> HostedToolPart:
    action = item.get("action") or {}
    query = action.get("query")
    status = item.get("status")
    return HostedToolPart(
        kind="web_search",
        call_id=item.get("id"),
        query=query if isinstance(query, str) else None,
        sources=tuple(
            Source(url=s["url"], title=s.get("title"))
            for s in action.get("sources") or ()
            if isinstance(s, Mapping) and s.get("url")
        ),
        error=None if status in (None, "completed") else str(status),
    )


def _parts_from_items(
    items: Sequence[Mapping[str, Any]], *, provider: str, model: str
) -> tuple[list[ContentPart], bool]:
    """Output items -> parts, in order. Also reports whether the model refused."""
    parts: list[ContentPart] = []
    refused = False
    for item in items:
        kind = item.get("type")
        if kind == "reasoning":
            summary = item.get("summary") or ()
            parts.append(
                ThinkingPart(
                    text="\n".join(
                        s.get("text", "") for s in summary if isinstance(s, Mapping)
                    ),
                    provider=provider,
                    model=model or None,
                )
            )
        elif kind == "message":
            for content in item.get("content") or ():
                if content.get("type") == "output_text":
                    parts.append(
                        TextPart(
                            text=content["text"],
                            citations=_citations(content.get("annotations")),
                        )
                    )
                elif content.get("type") == "refusal":
                    refused = True
                    parts.append(TextPart(text=content.get("refusal") or ""))
        elif kind == "function_call":
            arguments, raw_arguments, parse_error = parse_arguments(item.get("arguments"))
            parts.append(
                ToolCallPart(
                    id=item.get("call_id") or item["id"],
                    tool_name=item.get("name") or "unknown",
                    arguments=arguments,
                    raw_arguments=raw_arguments,
                    parse_error=parse_error,
                )
            )
        elif kind == "web_search_call":
            parts.append(_search_part(item))
        # Anything else stays only in Origin.raw: preserved for replay, not modelled.
    return parts, refused


def _stop_reason(body: Mapping[str, Any], parts: list[ContentPart], refused: bool) -> StopReason:
    status = body.get("status")
    if status == "incomplete":
        reason = str((body.get("incomplete_details") or {}).get("reason"))
        stop = _INCOMPLETE.get(reason, StopReason.UNKNOWN)
    elif status == "completed":
        stop = StopReason.REFUSAL if refused else StopReason.END_TURN
    else:
        stop = StopReason.UNKNOWN
    if any(isinstance(p, ToolCallPart) for p in parts) and stop is not StopReason.MAX_TOKENS:
        stop = StopReason.TOOL_USE  # Rule 1: content wins, unless truncated
    return stop


def _error_from_body(error: Any, *, provider: str) -> ProviderError:
    if isinstance(error, Mapping):
        code, message = error.get("code"), str(error.get("message") or error)
    else:
        code, message = None, str(error)
    return _RETRYABLE_CODES.get(str(code), ProviderError)(
        message,
        provider=provider,
        status_code=200,
        # A failed body is still an answer: the request was sent.
        phase=FailurePhase.BODY,
        request_sent=True,
    )


def response_from_wire(
    body: Mapping[str, Any], *, provider: str, quirks: Quirks
) -> ModelResponse:
    """A Responses body -> the canonical response."""
    if body.get("status") == "failed" or body.get("error"):
        raise _error_from_body(body.get("error") or "response failed", provider=provider)
    try:
        return _normalize(body, provider=provider, quirks=quirks)
    except (ValidationError, TypeError, AttributeError, KeyError, ValueError) as exc:
        raise ResponseInvalid(
            f"{provider} returned a response we cannot normalize "
            f"(status={body.get('status')!r}): {exc}",
            provider=provider,
            phase=FailurePhase.BODY,
            request_sent=True,
        ) from exc


def _normalize(body: Mapping[str, Any], *, provider: str, quirks: Quirks) -> ModelResponse:
    status = body.get("status")
    if status not in ("completed", "incomplete"):
        # queued / in_progress / cancelled: nothing a stateless call can use.
        raise ResponseInvalid(
            f"{provider} returned status {status!r}",
            provider=provider,
            phase=FailurePhase.BODY,
            request_sent=True,
        )
    model = body.get("model", "")
    items = [dict(item) for item in body.get("output") or ()]
    parts, refused = _parts_from_items(items, provider=provider, model=model)
    reason = (body.get("incomplete_details") or {}).get("reason")
    return ModelResponse(
        content=tuple(parts),
        stop_reason=_stop_reason(body, parts, refused),
        raw_stop_reason=f"{status}:{reason}" if reason else status,
        usage=usage_from_wire(body.get("usage"), quirks),
        model=model,
        provider=provider,
        request_id=body.get("id"),
        origin=Origin(provider=provider, api=API, model=model, raw=tuple(items)),
    )


RESPONSES = Codec(
    api="responses",
    path="/responses",
    request_to_wire=request_to_wire,
    response_from_wire=response_from_wire,
    hosted_tools=HOSTED_TOOLS,
)
