# `agentloop.providers.openrouter`

OpenRouter integration using the shared OpenAI-compatible HTTP client. Requires
`based-models-agentloop[openai-compat]`.

## Constants

- `BASE_URL = "https://openrouter.ai/api/v1"`
- `DEFAULT_MODEL = "openrouter/free"`
- `TIMEOUT_SECONDS`: connect/read/write/pool timeout mapping.

## `build_openrouter_client`

```text
build_openrouter_client(
    *,
    api_key,
    model="openrouter/free",
    app_url=None,
    app_name=None,
    http_client=None,
) -> OpenAICompatClient
```

Constructs an `LLMClient` named `openrouter`. `app_url` and `app_name` set OpenRouter's
optional attribution headers. `http_client` is an `httpx.Client` carrying your trust store,
proxy or transport; it stays yours to close, and anything else raises `ConfigurationError`.

```python
client = build_openrouter_client(
    api_key="secret",
    app_url="https://example.com",
    app_name="Example",
)
```

It speaks Chat Completions (`api="completions"`) and has no hosted tools
(`hosted_tools` is empty). For reasoning models it replays `reasoning_details` from the
message's `Origin`, unchanged and in order.

The returned client exposes `name`, `model`, `complete(request)`, `close()`, and context
manager methods. Add `RetryingClient` explicitly or construct through `build_client` for
automatic retries.
