# `agentloop.loop`

The provider-independent reason/act/observe loop.

## Constants

- `DEFAULT_MAX_TOKENS = 16_000`
- `MAX_TOKENS_CEILING = 64_000`
- `DEFAULT_MAX_ITERATIONS = 50`
- `DEFAULT_DEADLINE_SECONDS = 600.0`

## `run_conversation`

```text
run_conversation(
    transcript,
    client,
    registry,
    deps,
    *,
    system=None,
    max_tokens=16_000,
    max_iterations=50,
    deadline_seconds=600.0,
    observer=None,
) -> Transcript
```

Mutates and returns `transcript`. Each iteration sends a canonical `ModelRequest`, appends
the response, executes requested tools, and appends their results. A response truncated
mid-tool-call is discarded and retried with a doubled token allowance up to the ceiling.

The deadline is checked between turns. A provider call, its retries, or a started tool batch
may finish after it. `None` disables the deadline. Exhaustion raises `LoopBudgetExceeded`;
provider errors propagate unchanged.

```python
from agentloop import Deps, Registry, Transcript, run_conversation

transcript = Transcript()
transcript.add_user_message("hello")
run_conversation(transcript, client, Registry([]), Deps())
```

## `run_conversation_result`

Same parameters as `run_conversation`, returning a `ConversationResult` (`.transcript`,
`.summary`, `.text`). Both functions also accept `agent_name`, `conversation_id`,
`correlation_id` and `parent_id`, which label the summary, and `summary_options`
(a `SummaryOptions`: timeline, stall threshold, budget warnings). See
[Conversation summaries](../guides/summaries.md).

## `ListObserver`

An observer for tests and debugging. Public attribute `events` contains ordered
`(kind, payload)` tuples. It implements `on_request`, `on_response`, `on_retry`,
`on_tool_start`, and `on_tool_end` by appending events.

```python
observer = ListObserver()
# pass observer to an Agent or run_conversation
assert observer.kinds() == [kind for kind, _ in observer.events]
```

`kinds() -> list[str]` returns just the event names.
