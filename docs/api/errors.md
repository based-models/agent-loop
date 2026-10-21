# `agentloop.errors`

All library-defined failures inherit `AgentLoopError`.

```text
AgentLoopError
├── TranscriptError
├── ToolError
├── LoopBudgetExceeded
├── ConfigurationError (also ValueError)
└── ProviderError
    ├── AuthError
    ├── BadRequestError
    │   └── ContextLengthExceeded
    ├── ModelNotFound
    ├── InsufficientCredits
    ├── UnexpectedRedirect
    ├── RateLimited
    ├── ProviderUnavailable
    │   └── ProviderUnreachable
    ├── ProviderTimeout
    └── ResponseInvalid
```

## Non-provider errors

- `TranscriptError`: a transcript is inconsistent or unsafe to send.
- `ToolError`: a handler failure whose message is intended for the model. Raise this inside
  application tools; the executor converts it into an error result.
- `LoopBudgetExceeded`: the iteration, elapsed-time, or output-token limit was reached.
  Its `kind` (`"iterations"`, `"deadline"`, or `"max_tokens"`), `limit`, and `actual`
  attributes identify the limit without requiring message parsing.
- `ConfigurationError`: required configuration is absent or contradictory.

## `ProviderError`

```text
ProviderError(
    message,
    *,
    provider=None,
    status_code=None,
    retry_after=None,
    request_id=None,
    phase=None,
    request_sent=None,
    redirect_host=None,
)
```

In addition to its constructor fields, a `ProviderError` exposes `attempts` (1 by default),
`retry_wait_seconds` (0.0 by default), and `attempt_log` (empty by default). `RetryingClient`
fills in these fields when a model request fails. The class attribute `retryable` is `True`
for `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`, and `ResponseInvalid`.
`ProviderUnreachable` overrides it to `False`.

### Where it failed, and whether the request was sent

| Attribute | Meaning |
|---|---|
| `phase` | A `FailurePhase`: `connect`, `tls`, `proxy`, `send`, `read`, `status`, `redirect` or `body`. `None` when unknown. |
| `request_sent` | `False`: nothing left this machine, so nothing was billed. `True`: the request may have reached the provider. `None`: the adapter could not tell. `None` is not `False`. |
| `redirect_host` | For `UnexpectedRedirect`: the target's host only, never a path or query. |
| `attempt_log` | One `AttemptRecord` per attempt, set by `RetryingClient`. The loop supplies a one-record log for a client that is not wrapped. |

`ProviderUnreachable` (TLS verification, DNS, proxy refusal) and `UnexpectedRedirect` are not
retryable: they are faults in the environment, and a retry fails the same way. A refused or
reset connection and a read error stay retryable.

`InsufficientCredits` covers OpenRouter's 402 and an OpenAI 429 whose error `type`/`code` is
`insufficient_quota` or `credit_balance_exhausted`. The latter is a 429 only in name: it is
not retried, since waiting does not add credit.

An `AgentLoopError` raised from an active conversation also carries its
`ConversationSummary` as `.summary`. Errors raised before a conversation starts have
`.summary = None`.

This example leaves out the OpenAI key, so `build_client()` raises `AuthError`, a subtype
of `ProviderError`, before sending a request:

```python
from agentloop import Settings, build_client
from agentloop.errors import ProviderError

try:
    build_client(Settings(provider="openai"))
except ProviderError as error:
    print(type(error).__name__, str(error))
    print(error.status_code, error.request_id, error.summary)
```

Output:

```text
AuthError OPENAI_API_KEY is not set
None None None
```

The metadata fields are `None` here because no provider request or conversation began.
For errors from a model request, inspect `status_code`, `phase`, `request_sent`, and
`attempt_log` to understand what happened.
