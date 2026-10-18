# `agentloop.providers.modal`

Integration for a self-deployed vLLM OpenAI-compatible endpoint on Modal. Requires
`based-models-agentloop[openai-compat]`.

## Constants

- `DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"`
- `TIMEOUT_SECONDS`: extended connect/read timeouts for cold starts.
- `QUIRKS`: compatibility flags for vLLM usage, errors, and parallel tool calls.

## `build_modal_client`

```text
build_modal_client(
    *,
    endpoint_url,
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    proxy_token_id="",
    proxy_token_secret="",
    http_client=None,
) -> OpenAICompatClient
```

Appends `/v1` to `endpoint_url` after removing trailing slashes. Proxy credentials are used
only when both values are nonempty. `Settings` enforces the both-or-neither rule; this
low-level constructor itself treats an incomplete pair as unauthenticated. `http_client` is
an `httpx.Client` carrying your trust store, proxy or transport; it stays yours to close, and
anything else raises `ConfigurationError`.

```python
client = build_modal_client(
    endpoint_url="https://example--model.modal.run",
    proxy_token_id="id",
    proxy_token_secret="secret",
)
```

The returned client exposes `name`, `model`, `complete(request)`, `close()`, and context
manager methods. Construct through `build_client` or wrap with `RetryingClient` for retries.
