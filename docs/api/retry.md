# `agentloop.retry`

Provider-independent retry behavior. `RetryingClient` wraps any `LLMClient` and makes
another provider request when a failure is classified as retryable.

## Constants

`JITTER_SECONDS = 0.5` is the maximum random jitter added to exponential backoff.

## `RetryPolicy`

A frozen Pydantic model:

| Field | Type | Default |
|---|---|---|
| `max_attempts` | `int >= 1` | `3` |
| `base_delay` | positive `float` | `1.0` |
| `max_delay` | positive `float` | `30.0` |

`max_attempts` includes the initial request. A value of 3 allows at most two retries.

## `RetryingClient`

```text
RetryingClient(inner, *, policy=None, observer=None)
```

Implements `LLMClient` by delegating to `inner` and retrying `ProviderError` instances whose
`retryable` class attribute is true. Other exceptions propagate without a retry.

- `name` and `model` are read-only properties delegated to the inner client. The wrapper
  also exposes its hosted-tool support and optional `api` value.
- `complete(request)` returns the first successful response. Before each retry it calls
  `observer.on_retry(attempt, error)` and sleeps.
- `close()` calls the inner client's `close()` when present.

A successful response after a retry, and a terminal error, carry `attempt_log`: one frozen
`AttemptRecord` per attempt (`attempt`, `ok`, `error_type`, `status_code`, `phase`,
`request_sent`, `request_id`, `wait_seconds`). It holds names and ids only, never the error
message, so it is safe to log. It is empty when the first attempt succeeded. Use it to answer
"did any attempt reach the provider?".

Between attempts, the delay doubles from `base_delay` and includes up to `JITTER_SECONDS`
of random jitter. A provider's `retry_after` value takes precedence. Either way, the delay
is capped at `max_delay`.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses an
OpenAI client. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

`FailOnce` can wrap any `LLMClient`; here it wraps OpenAI. It raises one retryable error
before the request is sent, then lets OpenAI answer:

```python
import os

from agentloop import Agent
from agentloop.errors import ProviderUnavailable
from agentloop.providers.openai import build_openai_client
from agentloop.retry import RetryingClient, RetryPolicy


class FailOnce:
    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            raise ProviderUnavailable("temporary connection failure", request_sent=False)
        return self.inner.complete(request)


with build_openai_client(api_key=os.environ["OPENAI_API_KEY"]) as openai:
    inner = FailOnce(openai)
    client = RetryingClient(
        inner,
        policy=RetryPolicy(max_attempts=2, base_delay=0.01, max_delay=0.01),
    )
    result = Agent([], client=client).run_result("Reply with exactly ready.")

print("answer:", result.text)
print("attempts:", inner.calls)
print("retries:", result.summary.provider_retries)
```

Output:

```text
temporary connection failure (attempt 1/2); retrying in 0.0s
answer: ready
attempts: 2
retries: 1
```

The wrapper makes two calls to `FailOnce`; only the second reaches OpenAI. The summary
counts one extra provider attempt.
