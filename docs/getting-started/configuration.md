# Configuration

Use `Settings` when you want to validate configuration separately from constructing an
agent:

```python
import os

from agentloop import Settings, build_client

settings = Settings.from_env(os.environ)
client = build_client(settings)
try:
    print(client.name, client.model)
finally:
    client.close()
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `openrouter`, or `modal` |
| `LLM_MAX_ATTEMPTS` | `3` | Total attempts, including the first request |
| `LLM_TIMEOUT_SECONDS` | provider default | Read timeout for one model call (Anthropic: the SDK's overall timeout) |
| `ANTHROPIC_API_KEY` | unset | Anthropic credential |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Anthropic model |
| `OPENAI_API_KEY` | unset | OpenAI credential |
| `OPENAI_MODEL` | `gpt-5.1` | OpenAI model |
| `OPENROUTER_API_KEY` | unset | OpenRouter credential |
| `OPENROUTER_MODEL` | `openrouter/free` | OpenRouter model or router |
| `MODAL_ENDPOINT_URL` | unset | Base URL of the deployed endpoint |
| `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | Model exposed by Modal/vLLM |
| `MODAL_PROXY_TOKEN_ID` | unset | Optional Modal proxy token ID |
| `MODAL_PROXY_TOKEN_SECRET` | unset | Optional Modal proxy token secret |

You can construct `Settings` directly, which is useful when configuration comes from a
secrets service rather than environment variables:

```python
from pydantic import SecretStr

from agentloop import Settings

settings = Settings(
    provider="openai",
    openai_api_key=SecretStr("secret supplied at runtime"),
    openai_model="gpt-5.1",
    max_attempts=2,
)
```

See the [`config` API](../api/config.md) for every field.
