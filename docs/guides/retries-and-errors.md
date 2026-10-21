# Retries and errors

When a model call fails, its provider adapter raises a `ProviderError`. The error type tells
the retry wrapper whether another attempt might help. If a later attempt succeeds, the agent
returns an answer and records the retry in its summary. If every attempt fails, the final
error reaches your application.

`Agent.from_env()` installs a `RetryingClient` automatically. If you build a provider client
yourself, you can wrap it with `RetryingClient` and choose a `RetryPolicy`.

## Which failures get another attempt?

- **Temporary problems are retried:** `RateLimited`, `ProviderUnavailable`,
  `ProviderTimeout`, and `ResponseInvalid`. These cover rate limits, temporary server or
  connection failures, timeouts, and responses the adapter could not normalize.
- **Problems that need a change are not retried:** `AuthError`, `BadRequestError`,
  `ContextLengthExceeded`, `ModelNotFound`, and `InsufficientCredits`. Retrying the same
  credentials or request will not fix them.
- **Environment failures are not retried:** `ProviderUnreachable` covers DNS, TLS
  verification, and proxy failures. `UnexpectedRedirect` covers an unexpected HTTP redirect.

`ProviderUnreachable` is a non-retryable subclass of `ProviderUnavailable`. A refused or
reset connection can still be a temporary `ProviderUnavailable` and be retried. OpenAI 429
responses with `insufficient_quota` or `credit_balance_exhausted` are classified as
`InsufficientCredits`, so they are not retried.

## How many times will it try?

`RetryPolicy.max_attempts` counts the first call. The default is three attempts. Between
attempts, the wrapper uses exponential backoff with jitter, capped by `max_delay`. If the
provider supplies `Retry-After`, that wait is also capped by `max_delay`.

## A retry, then an answer

To make a retry visible, `FailOnce` wraps a client that implements `LLMClient`. It can wrap
any such client; this example passes it an OpenAI client. Its first call raises a retryable
connection error before sending anything, and its second call delegates to the wrapped
client. The `RetryPolicy` allows two attempts and keeps the wait short. Configure OpenAI as
in [Hello World](../getting-started/hello-world.md):

```python
import os

from agentloop import Agent
from agentloop.errors import FailurePhase, ProviderUnavailable
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
            raise ProviderUnavailable(
                "temporary connection failure",
                phase=FailurePhase.CONNECT,
                request_sent=False,
            )
        return self.inner.complete(request)


with build_openai_client(api_key=os.environ["OPENAI_API_KEY"]) as openai:
    inner = FailOnce(openai)
    client = RetryingClient(
        inner,
        policy=RetryPolicy(max_attempts=2, base_delay=0.01, max_delay=0.02),
    )
    result = Agent([], client=client).run_result("Reply with exactly ready.")

print("answer:", result.text)
print("attempts:", inner.calls)
print("provider retries:", result.summary.provider_retries)
print("first request sent:", result.summary.failed_attempts[0].request_sent)
```

Output:

```text
temporary connection failure (attempt 1/2); retrying in 0.0s
answer: ready
attempts: 2
provider retries: 1
first request sent: False
```

The first call fails with `request_sent=False`. `RetryingClient` makes one more call, which
returns OpenAI's answer. `result.summary.provider_retries` counts the extra attempt, while
`failed_attempts` keeps metadata about the first one.

## If all attempts fail

`RetryingClient` raises the final `ProviderError`. It carries `attempts` and an `attempt_log`
with one record per attempt. An error raised from an agent run also carries a
`ConversationSummary` in `error.summary`, including the retry count. Catch a specific error,
such as `RateLimited`, when your application can handle it; catch `AgentLoopError` at an outer
boundary for any library error. See the [error reference](../api/errors.md) for the hierarchy.

## Was the request sent?

For a `ProviderError`, inspect `request_sent` and `phase` rather than parsing its message.
`phase` names the stage that failed, such as `connect`, `read`, or `status`.

| `request_sent` | What you know |
|---|---|
| `False` | The request did not reach the provider. |
| `True` | It may have reached the provider. |
| `None` | The adapter could not determine whether it reached the provider. |

The example's first attempt failed during `connect`, so `request_sent` is `False`. On a
failed run, `error.summary.error_attempts_sent` counts attempts that may have reached the
provider. A value of `0` means none did.

See the [`retry` API](../api/retry.md) and [`errors` API](../api/errors.md).
