# `agentloop.observer`

Callbacks for conversation, model-request, retry, and tool events.

## `LoopObserver`

A protocol with these methods:

| Method | Called when |
|---|---|
| `conversation()` | Enters a context covering one complete conversation |
| `on_request(request)` | Immediately before a provider call |
| `on_response(response)` | After a successful provider call |
| `on_retry(attempt, error)` | Before retrying a provider failure |
| `on_tool_start(call)` | Before a tool handler runs |
| `on_tool_end(call, result)` | After a tool produces its paired result |
| `on_conversation_start(context)` | Once, right after `conversation()` is entered, with the ids and limits; optional |
| `on_conversation_end(summary)` | Once per conversation, on success or failure, inside `conversation()`; optional |
| `on_budget_warning(kind, used, limit)` | A configured share of a limit has been used; optional |
| `on_stall(tool_name, count)` | A tool repeated the same call to the configured threshold; optional |
| `on_model_error(error)` | A model call failed for good (not retryable, or retries exhausted), before the exception leaves the loop; optional |
| `on_text_delta(text)` | Reserved for streaming; unused by the current loop |

`conversation()` returns `AbstractContextManager[None]`. An observer can use it to create and
clean up state for one conversation.

## `NullObserver`

Implements every protocol method as a no-op and returns `nullcontext()` from
`conversation()`. Subclass it when only some events matter:

```python
class RetryCounter(NullObserver):
    def __init__(self) -> None:
        self.count = 0

    def on_retry(self, attempt: int, error: Exception) -> None:
        self.count += 1
```

## `ConsoleObserver`

Subclasses `NullObserver`. `on_response()` prints nonblank text parts;
`on_tool_start()` and `on_tool_end()` print tool activity; `on_retry()` prints retry details.
Because it prints the final answer, do not also print `Agent.run()` unless duplicate output
is wanted.

## `MultiObserver`

```text
MultiObserver(*observers)
```

Calls each observer in argument order. `conversation()` starts them in argument order and
closes them in reverse order. A failed callback in one observer does not prevent the others
from receiving the event.

## `GuardedObserver`

```text
GuardedObserver(inner)
```

Uses the wrapped observer's `conversation()` context directly. All `on_*` methods catch and
log exceptions from the wrapped observer so reporting failures do not interrupt agent work.

## `guarded`

```text
guarded(observer: LoopObserver | None) -> LoopObserver
```

Returns `NullObserver` for `None`. It leaves an existing `GuardedObserver` or exact
`NullObserver` unchanged and wraps other observers. Calling it more than once does not add
more wrappers.
