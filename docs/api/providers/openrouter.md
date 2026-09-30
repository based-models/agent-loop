# `agentloop.providers.openrouter`

OpenRouter adapter using the shared OpenAI-compatible transport. Requires
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
) -> OpenAICompatClient
```

Constructs an `LLMClient` named `openrouter`. `app_url` and `app_name` set OpenRouter's
optional attribution headers.

```python
client = build_openrouter_client(
    api_key="secret",
    app_url="https://example.com",
    app_name="Example",
)
```

The returned client exposes `name`, `model`, `complete(request)`, `close()`, and context
manager methods. Add `RetryingClient` explicitly or construct through `build_client` for
automatic retries.
