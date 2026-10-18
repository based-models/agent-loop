# `agentloop.retry`

Provider-independent retry behavior.

## Constants

`JITTER_SECONDS = 0.5` is the maximum random jitter added to exponential backoff.

## `RetryPolicy`

A frozen Pydantic model:

| Field | Type | Default |
|---|---|---|
| `max_attempts` | `int >= 1` | `3` |
| `base_delay` | positive `float` | `1.0` |
| `max_delay` | positive `float` | `30.0` |

## `RetryingClient`

```text
RetryingClient(inner, *, policy=None, observer=None)
```

Implements `LLMClient` by delegating to `inner` and retrying `ProviderError` instances whose
`retryable` class attribute is true.

- `name` and `model` are read-only properties delegated to the inner client.
- `complete(request)` returns the first successful response. Before each retry it calls
  `observer.on_retry(attempt, error)` and sleeps.
- `close()` calls the inner client's `close()` when present.

A successful response after a retry, and a terminal error, carry `attempt_log`: one frozen
`AttemptRecord` per attempt (`attempt`, `ok`, `error_type`, `status_code`, `phase`,
`request_sent`, `request_id`, `wait_seconds`). It holds names and ids only, never the error
message, so it is safe to log. It is empty when the first attempt succeeded. Use it to answer
"did any attempt reach the provider?".

`max_attempts` includes the initial request. `retry_after` is preferred over exponential
backoff, but both are capped at `max_delay`.

```python
policy = RetryPolicy(max_attempts=2, base_delay=0.1, max_delay=1.0)
client = RetryingClient(raw_client, policy=policy)
```
