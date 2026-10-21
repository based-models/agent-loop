# `agentloop.providers.modal`

Integration for a self-deployed vLLM OpenAI-compatible endpoint on Modal. Install
`based-models-agentloop[openai-compat]` and set `MODAL_ENDPOINT_URL` to your deployment's
base URL. For automatic provider selection and retries, see the
[providers guide](../../guides/providers.md).

## Constants

- `DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"`
- `TIMEOUT_SECONDS`: connect 60, read 900, write 30, and pool 10 seconds. The longer
  connect and read timeouts allow for cold starts.
- `QUIRKS`: the adapter does not expect detailed usage fields or JSON error bodies from
  this endpoint, and disables parallel tool calls by default.

## `build_modal_client`

```text
build_modal_client(
    *,
    endpoint_url,
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    proxy_token_id="",
    proxy_token_secret="",
    http_client=None,
    timeout_seconds=None,
) -> OpenAICompatClient
```

The builder returns a direct `LLMClient` named `modal`. It removes trailing slashes from
`endpoint_url`, adds `/v1`, and sends Chat Completions requests to
`/v1/chat/completions`. It has no hosted tools (`hosted_tools` is empty).

If both proxy tokens are nonempty, the builder sends them as `Modal-Key` and
`Modal-Secret` headers. With one or both tokens empty, it sends neither header.
`Settings` rejects an incomplete pair when you use `build_client()`; this lower-level
builder does not. `timeout_seconds` overrides the 900-second read timeout while retaining
the other timeout defaults.

This example builds a client for the configured endpoint and inspects its public
properties without sending a model request:

```python
import os

from agentloop.providers.modal import build_modal_client

with build_modal_client(
    endpoint_url=os.environ["MODAL_ENDPOINT_URL"],
    proxy_token_id=os.getenv("MODAL_PROXY_TOKEN_ID", ""),
    proxy_token_secret=os.getenv("MODAL_PROXY_TOKEN_SECRET", ""),
) as client:
    print(client.name, client.api)
    print(client.model)
    print(sorted(client.hosted_tools))
```

Output:

```text
modal completions
Qwen/Qwen3.6-35B-A3B-FP8
[]
```

The client also exposes `complete(request)` and `close()`. Pass an `httpx.Client` as
`http_client` to configure a trust store, proxy, or transport. The client remains yours to
close, and `close()` leaves it open. Passing another HTTP client type raises
`ConfigurationError`. The builder does not add retries; use `build_client()` or
`RetryingClient` if you need them.
