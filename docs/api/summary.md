# `agentloop.summary`

Structured results for a completed or failed conversation. Summaries contain metadata, not
prompts, model responses, tool arguments, tool results, or exception messages.

## `ConversationStatus`

Describes how a conversation ended: `completed`, `refused`, `truncated`,
`iteration_limit`, `deadline_exceeded`, `provider_error`, `error`, or `cancelled`.

Use `status.succeeded` to check whether the call returned normally and
`status.budget_exhausted` to check whether it reached an iteration or time limit.

## `ConversationSummary`

A frozen, JSON-serializable record produced once for every conversation. It includes:

- conversation, correlation, and parent identifiers;
- agent, provider, and model names;
- status, iterations, configured limits, and elapsed time;
- model, tool, and retry-wait time;
- tool calls, tool errors, retries, token use, and provider-reported cost;
- repeated-call and stall information; and
- an optional tuple of `TurnRecord` entries.

`None` means that a value was not reported or configured. It does not mean zero. For
example, `usage.cost_usd` is `None` when the provider did not report cost.

## `ConversationResult`

A frozen object containing `.transcript` and `.summary`. Its `.text` property returns the
final assistant text.

`Agent.run_result`, `Agent.converse_result`, and `run_conversation_result` return this type.

## `SummaryOptions`

```text
SummaryOptions(
    *,
    timeline=False,
    stall_threshold=None,
    warn_at=(),
)
```

- `timeline`: include one `TurnRecord` per model call.
- `stall_threshold`: report a stall after the same tool is requested with identical
  arguments this many times. The minimum is 2; `None` disables stall events.
- `warn_at`: fractions of the iteration and time limits that trigger budget warnings, such
  as `(0.5, 0.8)`.

These options report conditions but do not stop or otherwise change the conversation.

## `TurnRecord`

Metadata for one model call: duration, stop reason, token use, cost, attempts, tools, tool
errors, and whether a truncated call was discarded. When a call fails before returning,
`error_type` contains the exception class name.

## `ConversationContext`

The identifiers, names, and limits passed to an observer when a conversation starts.

See [Conversation summaries](../guides/summaries.md) for examples and operational guidance.
