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
| `max_attempts` | `int >= 1` | `3` |

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
build_client(settings, *, observer=None) -> LLMClient
```

Constructs the selected adapter and wraps it in `RetryingClient` using
`settings.max_attempts`. The optional observer receives retry events. Raises `AuthError` for
missing required credentials and `ConfigurationError` for incomplete Modal configuration
or an unsupported provider.
