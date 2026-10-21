# Configuration

Use `Settings` to validate a mapping before constructing a client. This example creates an
OpenAI client with a placeholder credential; constructing and closing it makes no model
request:

```python
from agentloop import Settings, build_client

settings = Settings.from_env({
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "example-key",
    "OPENAI_MODEL": "example-model",
    "LLM_MAX_ATTEMPTS": "2",
})
client = build_client(settings)
try:
    print(client.name, client.model)
    print(f"max attempts: {settings.max_attempts}")
finally:
    client.close()
```

Output:

```text
openai example-model
max attempts: 2
```

The application supplies the mapping. Pass `os.environ` when you want process environment
variables; `Settings.from_env()` does not read them on its own. `build_client()` applies
the configured retry policy, and the caller closes the client it created.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` | `anthropic`, `openai`, `openrouter`, or `modal` |
| `LLM_MAX_ATTEMPTS` | `3` | Total attempts, including the first request |
| `LLM_TIMEOUT_SECONDS` | provider default | Read timeout for one model call (Anthropic: the SDK's overall timeout) |
| `ANTHROPIC_API_KEY` | unset | Anthropic credential |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Anthropic model |
| `OPENAI_API_KEY` | unset | OpenAI credential |
| `OPENAI_MODEL` | `gpt-5.1` | OpenAI model |
| `OPENAI_API` | `responses` | `responses` or `completions` |
| `OPENROUTER_API_KEY` | unset | OpenRouter credential |
| `OPENROUTER_MODEL` | `openrouter/free` | OpenRouter model or router |
| `MODAL_ENDPOINT_URL` | unset | Base URL of the deployed endpoint |
| `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | Model exposed by Modal/vLLM |
| `MODAL_PROXY_TOKEN_ID` | unset | Optional Modal proxy token ID |
| `MODAL_PROXY_TOKEN_SECRET` | unset | Optional Modal proxy token secret |

You can also construct `Settings` directly when values come from application configuration
or a secrets service:

```python
from pydantic import SecretStr

from agentloop import Settings

settings = Settings(
    provider="openai",
    openai_api_key=SecretStr("example-key"),
    openai_model="example-model",
    max_attempts=2,
)
print(settings.provider, settings.model, settings.max_attempts)
```

Output:

```text
openai example-model 2
```

The keys above are placeholders; no request is sent by either example. Replace them with
application-supplied credentials before calling a provider. `Settings` rejects unknown
fields, invalid provider names, and nonpositive attempt or timeout values.

See the [`config` API](../api/config.md) for every field.
