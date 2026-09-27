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
- `LoopBudgetExceeded`: iteration or between-turn wall-clock budget was exhausted.
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

Public attributes are `provider`, `status_code`, `retry_after`, `request_id`, and the class
attribute `retryable`. Only `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`, and
`ResponseInvalid` set `retryable = True`.

```python
from agentloop.errors import ProviderError

try:
    agent.run("hello")
except ProviderError as error:
    print(error.provider, error.status_code, error.request_id)
```
