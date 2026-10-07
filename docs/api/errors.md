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
    ├── RateLimited
    ├── ProviderUnavailable
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
)
```

Public attributes are `provider`, `status_code`, `retry_after`, `request_id`, `attempts`,
`retry_wait_seconds`, and the class attribute `retryable`. `RetryingClient` sets `attempts`
and `retry_wait_seconds`. Only `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`, and
`ResponseInvalid` set `retryable = True`.

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
