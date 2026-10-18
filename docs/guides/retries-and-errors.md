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

`ProviderUnreachable` (TLS verification, DNS, a refusing proxy) and `UnexpectedRedirect` are
not retried, because retrying cannot fix them. Both are faults in the environment. A refused
or reset connection and a read error are retried.

## Was the request sent?

For spend and safety, ask the error rather than inspecting its cause:

```python
from agentloop.errors import ProviderError

try:
    agent.run("Hello")
except ProviderError as error:
    if error.request_sent is False:
        print("nothing left this machine; nothing was billed")
    elif error.request_sent is None:
        print("unknown; assume it may have been billed")
    print(error.phase, [r.phase for r in error.attempt_log])
```

`ConversationSummary.error_attempts_sent` gives the same answer across every attempt: `0`
means no attempt reached the provider.

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
