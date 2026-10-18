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
    hosted_tools=(),
    agent_name=None,
    conversation_id=None,
    correlation_id=None,
    parent_id=None,
    summary_options=None,
) -> Transcript
```

Mutates and returns `transcript`. Each iteration sends a provider-independent
`ModelRequest`, appends the response, executes requested tools, and appends their results. A
response truncated mid-tool-call, or truncated before producing anything readable, is discarded
and retried with a doubled token allowance up to the 64,000 ceiling. A budget is never lowered,
and when it cannot grow the run raises `LoopBudgetExceeded` with `kind="max_tokens"`.

A provider that pauses a turn (`StopReason.PAUSED`, for example Anthropic's `pause_turn` while
a hosted tool runs) is continued without sending a user message: the continuation joins the
same assistant message, and each continuation counts as an iteration.

If `transcript` ends in tool calls with no results (a log cut off while tools ran), the loop
runs them before the first model call and then validates the transcript with
`check_invariants()`, so a bad one raises `TranscriptError` instead of a provider 400.

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

Accepts the same parameters as `run_conversation` and returns a `ConversationResult` with
`.transcript`, `.summary`, and `.text`. The agent and conversation identifiers label the
summary. `summary_options` configures the timeline, repeated-call detection, and limit
warnings. See [Conversation summaries](../guides/summaries.md).

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
