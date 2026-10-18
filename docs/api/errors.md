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
- `LoopBudgetExceeded`: the iteration or elapsed-time limit was reached. Its `kind`,
  `limit`, and `actual` attributes identify the limit without requiring message parsing.
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

Public attributes are `provider`, `status_code`, `retry_after`, `request_id`, `attempts`,
`retry_wait_seconds`, and the class attribute `retryable`. `RetryingClient` sets `attempts`
and `retry_wait_seconds`. Only `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`, and
`ResponseInvalid` set `retryable = True`; `ProviderUnreachable` overrides it to `False`.

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
`ConversationSummary` as `.summary`. Errors raised before a conversation starts may have
`.summary = None`.

```python
from agentloop.errors import ProviderError

try:
    agent.run("hello")
except ProviderError as error:
    print(error.provider, error.status_code, error.request_id)
```
