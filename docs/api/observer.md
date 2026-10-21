# `agentloop.observer`

An observer is an object that receives callbacks as the agent loop runs. The loop calls it
before and after model requests and local tool calls, when a request is retried, and when a
conversation ends. These callbacks let an application record what happened while the
conversation is still in progress.

Pass an observer to `Agent` or `run_conversation()` with `observer=...`. For example, an
observer can count model responses, log tool activity, or send timing data to a tracing or
metrics system. The `ResponseCounter` below shows the smallest useful case: it increments
a counter each time a model response arrives. Subclass `NullObserver` to implement only the
callbacks your application needs.

## `LoopObserver`

`LoopObserver` is the callback interface accepted by the `observer=` parameter. It is a
Python `Protocol`: it describes the methods an observer can provide and the values each
method receives. You normally create an observer by subclassing `NullObserver` and
overriding the callbacks you need, as `ResponseCounter` does below.

`conversation()` provides a context around the whole run. The `on_*` methods receive
individual events inside that context. The last six hooks in the table are optional at
runtime; if a custom observer omits them, the loop skips them. `NullObserver` supplies
no-op implementations of every hook.

| Method | Called when |
|---|---|
| `conversation() -> AbstractContextManager[None]` | Enters a context covering one complete conversation |
| `on_request(request: ModelRequest)` | Immediately before a provider call |
| `on_response(response: ModelResponse)` | After a successful provider call |
| `on_retry(attempt: int, error: Exception)` | Before retrying a provider failure; `attempt` is the failed attempt number |
| `on_tool_start(call: ToolCallPart)` | Before a local tool call is handled, including a call to an unknown tool |
| `on_tool_end(call: ToolCallPart, result: ToolResultPart)` | After that call produces a result, including an error result |
| `on_conversation_start(context: ConversationContext)` | Once, after `conversation()` is entered, with the IDs and limits; optional |
| `on_conversation_end(summary: ConversationSummary)` | Once per conversation, on success or failure, inside `conversation()`; optional |
| `on_budget_warning(kind: BudgetKind, used: float, limit: float)` | A configured share of the iteration or deadline limit has been used; optional |
| `on_stall(tool_name: str, count: int)` | A tool repeated the same call to the configured threshold; optional |
| `on_model_error(error: Exception)` | A model call failed after its final attempt, before the exception leaves the loop; optional |
| `on_text_delta(text: str)` | Reserved for streaming; unused by the current loop |

An observer can use `conversation()` to create and clean up state for one conversation.
`on_response` can run more than once in a conversation when the model calls tools; each
successful model call produces a response event. `on_conversation_end` runs once for the
whole conversation.

`on_model_error` includes failures that are not retryable, so it can fire without any
`on_retry` event.

## `NullObserver`

Implements every protocol method as a no-op and returns `nullcontext()` from
`conversation()`. Subclass it when only some events matter.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Count the successful model responses in one agent run:

```python
import os

from agentloop import Agent, NullObserver
from agentloop.provider import ModelResponse


class ResponseCounter(NullObserver):
    def __init__(self) -> None:
        self.count = 0

    def on_response(self, response: ModelResponse) -> None:
        self.count += 1


observer = ResponseCounter()
with Agent.from_env(os.environ, tools=[], observer=observer) as agent:
    answer = agent.run("How many days are in a week? Reply with one digit.")

print(answer)
print(observer.count)
```

Output:

```text
7
1
```

`on_response` runs after the provider returns. The counter records one response for this
conversation.

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
closes them in reverse order. A failed `on_*` callback in one observer does not prevent the
others from receiving the event. An exception from a `conversation()` context still
propagates.

## `GuardedObserver`

```text
GuardedObserver(inner)
```

Uses the wrapped observer's `conversation()` context directly, so exceptions from that
context propagate. All `on_*` methods catch and log exceptions from the wrapped observer
so reporting failures do not interrupt agent work.

## `guarded`

```text
guarded(observer: LoopObserver | None) -> LoopObserver
```

Returns `NullObserver` for `None`. It leaves an existing `GuardedObserver` or exact
`NullObserver` unchanged and wraps other observers. Calling it more than once does not add
more wrappers.
