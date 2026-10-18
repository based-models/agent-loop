# `agentloop.providers.openai`

OpenAI integration using the shared OpenAI-compatible HTTP client. Requires
`based-models-agentloop[openai-compat]`.

## Constants

- `BASE_URL = "https://api.openai.com/v1"`
- `DEFAULT_MODEL = "gpt-5.1"`
- `TIMEOUT_SECONDS`: connect/read/write/pool timeout mapping.
- `QUIRKS`: compatibility settings, including `max_completion_tokens` (Chat Completions
  only; Responses sends `max_output_tokens`).

## `build_openai_client`

```text
build_openai_client(
    *,
    api_key,
    model="gpt-5.1",
    organization=None,
    project=None,
    api="responses",
    http_client=None,
) -> OpenAICompatClient
```

Constructs an `LLMClient` named `openai`. Optional organization and project values become
OpenAI request headers.

`api` picks the wire format: `"responses"` (the default) posts to `/responses`, and
`"completions"` keeps Chat Completions. The client exposes it as `client.api`, and the
hosted tools it can run as `client.hosted_tools` (`web_search` on Responses, none on
Completions).

`http_client` is an `httpx.Client` carrying your trust store, proxy or transport. It stays
yours to close; `close()` leaves it open. Anything else raises `ConfigurationError`.

```python
with build_openai_client(api_key="secret", project="project-id") as client:
    response = client.complete(request)
```

The returned client exposes `name`, `model`, `complete(request)`, `close()`, and context
manager methods. Retry wrapping is not added by this constructor; use `build_client` or
`RetryingClient` when retries are required.
