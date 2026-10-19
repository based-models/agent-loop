# `agentloop.config`

Configuration models and provider-client construction.

## `Provider`

Type alias for `"anthropic" | "openrouter" | "modal" | "openai"`.

## `Settings`

A frozen Pydantic model. Unknown fields are rejected.

| Field | Type | Default |
|---|---|---|
| `provider` | `Provider` | `"anthropic"` |
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

### `Settings.model`

Read-only property returning the model selected by `provider`.

### `Settings.from_env`

```text
Settings.from_env(env) -> Settings
```

Translates the variables listed in the [configuration guide](../getting-started/configuration.md).
Empty secret values become `None`. Pydantic validates values such as `LLM_MAX_ATTEMPTS`.

```python
settings = Settings.from_env({
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "secret",
})
assert settings.model == "gpt-5.1"
```

## `build_client`

```text
build_client(settings, *, observer=None, http_client=None) -> LLMClient
```

Constructs the selected provider client and wraps it in `RetryingClient` using
`settings.max_attempts`. The optional observer receives retry events. Raises `AuthError` for
missing required credentials and `ConfigurationError` for incomplete Modal configuration,
an unsupported provider, or an `http_client` from the wrong package.

`http_client` carries your trust store, proxy or transport (see
[corporate networks](../guides/providers.md#corporate-networks-trust-proxy-and-transport)).
It is an `httpx2.Client` for `anthropic`, whose SDK is built on httpx2, and an
`httpx.Client` for `openai`, `openrouter` and `modal`. It stays yours to close: closing the
returned client leaves it open.
