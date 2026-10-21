# `agentloop.providers.anthropic`

Anthropic Messages API integration. The SDK is imported only when needed and requires
`based-models-agentloop[anthropic]`.

## Constants

- `PROVIDER = "anthropic"`
- `DEFAULT_MODEL = "claude-opus-5"`
- `DEFAULT_TIMEOUT_SECONDS = 600.0`
- `CONTEXT_OVERFLOW_STOP_REASON = "model_context_window_exceeded"`
- `API = "messages"`
- `HOSTED_TOOLS = frozenset({"web_search"})`: the hosted kinds this adapter supports.
- `WEB_SEARCH_BASIC = "web_search_20250305"`: the default web search tool.
- `WEB_SEARCH_FILTERED = "web_search_20260209"`: sent only when the hosted tool has
  `dynamic_filtering=True` (Claude 4.6+). Claude then runs a program that filters results,
  which may leave the response without citations even when it has sources; see the
  [hosted tools guide](../../guides/hosted-tools.md).

## Translation helpers

These public helpers convert between shared Agentloop types and the Anthropic API format:

- `tool_to_wire(tool) -> dict[str, Any]`: converts `ToolSchema` to an Anthropic tool.
- `message_to_wire(message) -> dict[str, Any]`: converts one provider-independent message to one
  Anthropic message.
- `request_to_wire(request, *, model) -> dict[str, Any]`: builds a Messages API payload.
- `hosted_tool_to_wire(tool) -> dict[str, Any]`: maps a `HostedTool` to the Anthropic tool
  declaration.
- `usage_from_wire(raw) -> Usage`: normalizes Anthropic usage fields.
- `response_from_wire(message) -> ModelResponse`: normalizes a response and may raise
  `ContextLengthExceeded` or `ResponseInvalid`, both with `phase=BODY` and
  `request_sent=True`: the server answered.
- `classify_sdk_error(exc) -> ProviderError`: maps an Anthropic SDK exception to the common
  hierarchy.

`request_to_wire` puts `ToolSchema.parameters` in `input_schema`, adds local and hosted
tools to the same `tools` list, and maps `tool_choice="required"` to Anthropic's `any`
when local tools are present. Hosted web search supports `allowed_domains`,
`blocked_domains`, `max_uses`, `user_location`, and `dynamic_filtering`. Supplying both
domain lists raises `ConfigurationError`; `search_context_size` is ignored with a warning.
It omits `temperature` because current Claude models reject that parameter. For usage,
Anthropic reports cache read and cache write tokens separately from input tokens.

This example builds a request and inspects its translated payload. It needs no API key or
network connection.

```python
from agentloop import HostedTool, Transcript
from agentloop.provider import ModelRequest
from agentloop.providers.anthropic import request_to_wire

transcript = Transcript()
transcript.add_user_message("Hello")
request = ModelRequest(
    messages=tuple(transcript.messages),
    hosted_tools=(HostedTool(kind="web_search"),),
    max_tokens=100,
)
payload = request_to_wire(request, model="claude-opus-5")

print(payload["model"])
print(payload["messages"][0]["content"][0]["text"])
print(payload["tools"][0]["type"])
```

Output:

```text
claude-opus-5
Hello
web_search_20250305
```

## `AnthropicClient`

```text
AnthropicClient(
    *,
    api_key=None,
    model="claude-opus-5",
    timeout=600.0,
    http_client=None,
)
```

Public class attribute `name = "anthropic"`; public instance attribute `model` contains the
chosen model.

- `complete(request) -> ModelResponse`: sends one request with SDK retries disabled and
  translates SDK and credential failures.
- `close() -> None`: closes the SDK connection pool and is safe to repeat. A client passed
  as `http_client` is left open.
- `__enter__()` returns the client; `__exit__()` calls `close()`.

If you provide `http_client`, it must use the HTTP client class expected by the installed
Anthropic SDK. The current SDK uses `httpx2.Client`; passing a client from another HTTP
package raises `ConfigurationError`. It carries your trust store, proxy or transport and
stays yours to close. The SDK follows redirects by default; pass a client with
`follow_redirects=False` to have a 3xx reported.

`pause_turn` maps to `StopReason.PAUSED`. Assistant turns are replayed from `Origin.raw`, so
encrypted search results and signatures go back exactly as received.

When `api_key=None`, credential resolution is delegated to the Anthropic SDK. You can set
`ANTHROPIC_API_KEY` or supply `api_key` explicitly. A missing credential raises `AuthError`
when `complete()` is called.
