# `agentloop.observer`

Lifecycle events emitted by the loop, retry wrapper, and tool executor.

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
| `on_budget_warning(kind, used, limit)` | A fraction in `SummaryOptions.warn_at` of the iteration limit or deadline is used; optional |
| `on_stall(tool_name, count)` | A tool was called with identical arguments `count` times (`SummaryOptions.stall_threshold`); optional |
| `on_text_delta(text)` | Reserved for streaming; unused by the current loop |

`conversation()` returns `AbstractContextManager[None]`. Implementations may use it to
allocate and finalize per-conversation state.

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

Fans every callback out in argument order. `conversation()` enters all child contexts and
unwinds them in reverse order. Each child is guarded independently so one broken callback
does not starve later observers.

## `GuardedObserver`

```text
GuardedObserver(inner)
```

Delegates `conversation()` directly. All `on_*` methods catch and log `Exception` from the
inner observer. This prevents telemetry failures from leaving tool calls unanswered.

## `guarded`

```text
guarded(observer: LoopObserver | None) -> LoopObserver
```

Returns `NullObserver` for `None`, preserves an already guarded observer or an exact
`NullObserver`, and otherwise returns `GuardedObserver(observer)`. The operation is
idempotent.
