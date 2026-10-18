# `agentloop.providers.anthropic`

Anthropic Messages API integration. The SDK is imported only when needed and requires
`based-models-agentloop[anthropic]`.

## Constants

- `PROVIDER = "anthropic"`
- `DEFAULT_MODEL = "claude-opus-5"`
- `DEFAULT_TIMEOUT_SECONDS = 600.0`
- `CONTEXT_OVERFLOW_STOP_REASON = "model_context_window_exceeded"`

## Translation helpers

These public helpers convert between shared Agentloop types and the Anthropic API format:

- `tool_to_wire(tool) -> dict[str, Any]`: converts `ToolSchema` to an Anthropic tool.
- `message_to_wire(message) -> dict[str, Any]`: converts one provider-independent message to one
  Anthropic message.
- `request_to_wire(request, *, model) -> dict[str, Any]`: builds a Messages API payload.
- `usage_from_wire(raw) -> Usage`: normalizes Anthropic usage fields.
- `response_from_wire(message) -> ModelResponse`: normalizes a response and may raise
  `ContextLengthExceeded` or `ResponseInvalid`, both with `phase=BODY` and
  `request_sent=True`: the server answered.
- `classify_sdk_error(exc) -> ProviderError`: maps an Anthropic SDK exception to the common
  hierarchy.

```python
payload = request_to_wire(request, model="claude-opus-5")
assert payload["model"] == "claude-opus-5"
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

`http_client` must be an `httpx2.Client`: the Anthropic SDK is built on httpx2, whose
classes are not httpx's. Any other object, an `httpx.Client` included, raises
`ConfigurationError`. It carries your trust store, proxy or transport and stays yours to
close. The SDK follows redirects by default; pass a client with `follow_redirects=False` to
have a 3xx reported.
- `__enter__()` returns the client; `__exit__()` closes it.

When `api_key=None`, credential resolution is delegated to the Anthropic SDK.
