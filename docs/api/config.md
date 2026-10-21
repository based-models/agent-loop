# `agentloop.config`

Configuration models and provider-client construction.

## `Provider`

Type alias for `"anthropic" | "openrouter" | "modal" | "openai"`.

## `Settings`

A frozen Pydantic model. Unknown fields are rejected. The field defaults are:

| Field | Type | Default |
|---|---|---|
| `provider` | `Provider` | `"openrouter"` |
| `anthropic_api_key` | `SecretStr | None` | `None` |
| `anthropic_model` | `str` | `"claude-opus-5"` |
| `openrouter_api_key` | `SecretStr | None` | `None` |
| `openrouter_model` | `str` | `"openrouter/free"` |
| `modal_endpoint_url` | `str` | `""` |
| `modal_endpoint_model` | `str` | `"Qwen/Qwen3.6-35B-A3B-FP8"` |
| `modal_proxy_token_id` | `SecretStr | None` | `None` |
| `modal_proxy_token_secret` | `SecretStr | None` | `None` |
| `openai_api_key` | `SecretStr | None` | `None` |
| `openai_model` | `str` | `"gpt-5.1"` |
| `openai_api` | `"responses" | "completions"` | `"responses"` |
| `max_attempts` | `int >= 1` | `3` |
| `timeout_seconds` | `float > 0 \| None` | `None` (provider default) |

`max_attempts` counts the first request and any retries. `timeout_seconds` applies to one
model call. With OpenAI, OpenRouter, and Modal it replaces the read timeout; with Anthropic
it sets the SDK's overall request timeout.

### `Settings.model`

Read-only property returning the model selected by `provider`. For example, with
`provider="openai"`, it returns `openai_model`.

### `Settings.from_env`

```text
Settings.from_env(env) -> Settings
```

Translates the variables listed in the [configuration guide](../getting-started/configuration.md).
Pass a mapping explicitly, such as `os.environ` or a dictionary. The method does not read
the process environment or a dotenv file on its own. Empty secret values become `None`,
and Pydantic validates values such as `LLM_MAX_ATTEMPTS`.

This example uses OpenAI settings. It uses a placeholder key because constructing and
closing the client sends no model request:

```python
from agentloop import Settings, build_client

settings = Settings.from_env({
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "example-key",
    "LLM_MAX_ATTEMPTS": "2",
})
print(settings.provider, settings.model)
print(f"max attempts: {settings.max_attempts}")

client = build_client(settings)
try:
    print(client.name, client.model)
finally:
    client.close()
```

Output:

```text
openai gpt-5.1
max attempts: 2
openai gpt-5.1
```

## `build_client`

```text
build_client(settings, *, observer=None, http_client=None) -> LLMClient
```

Constructs the selected provider client and wraps it in `RetryingClient` using
`settings.max_attempts`. The optional observer receives retry events. The caller owns the
returned client and should close it when finished, as in the example above.

Missing OpenAI or OpenRouter credentials raise `AuthError` during construction. Anthropic
may resolve credentials through its SDK, so a missing credential can instead raise
`AuthError` on the first model request. Incomplete Modal configuration or an `http_client`
from the wrong package raises `ConfigurationError`. Invalid `Settings` values, including an
unsupported provider, are rejected by Pydantic.

`http_client` carries your trust store, proxy or transport (see
[corporate networks](../guides/providers.md#corporate-networks-trust-proxy-and-transport)).
It is an `httpx2.Client` for `anthropic`, whose SDK is built on httpx2, and an
`httpx.Client` for `openai`, `openrouter` and `modal`. It stays yours to close: closing the
returned client leaves it open.
