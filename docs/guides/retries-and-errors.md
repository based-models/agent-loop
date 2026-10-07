# Retries and errors

Every provider integration raises the same set of error types. Catch a specific error when your
application can recover, or catch `AgentLoopError` at its outer boundary.

```python
from agentloop.errors import AgentLoopError, AuthError, RateLimited

try:
    answer = agent.run("Hello")
except AuthError:
    print("Check provider credentials")
except RateLimited as error:
    print("Provider remained rate limited after retries", error.retry_after)
except AgentLoopError as error:
    print(f"Agent failed: {type(error).__name__}: {error}")
```

`RateLimited`, `ProviderUnavailable`, `ProviderTimeout`, and `ResponseInvalid` are retryable.
Authentication, bad-request, context-length, missing-model, and credit errors are not.

Wrap a custom client with an explicit policy:

```python
from agentloop.retry import RetryingClient, RetryPolicy

client = RetryingClient(
    raw_client,
    policy=RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=10.0),
)
```

`max_attempts` includes the first request. Server `Retry-After` values are honored but
capped by `max_delay`; otherwise exponential backoff with jitter is used.

See the [`retry` API](../api/retry.md) and [`errors` API](../api/errors.md).
