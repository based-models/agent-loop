# `agentloop.summary`

Structured results for a completed or failed conversation. Summaries contain metadata, not
prompts, model responses, tool arguments, tool results, or exception messages.

## `ConversationStatus`

Describes how a conversation ended. The `succeeded` and `budget_exhausted` properties are
computed from the status; they are not additional status values.

| Status | Meaning | `succeeded` | `budget_exhausted` |
|---|---|---|---|
| `completed` | The model finished its answer | `True` | `False` |
| `refused` | The model declined the request | `True` | `False` |
| `truncated` | The final answer reached `max_tokens` | `True` | `False` |
| `iteration_limit` | The loop used all allowed iterations | `False` | `True` |
| `deadline_exceeded` | The loop reached its time limit | `False` | `True` |
| `token_limit` | A truncated tool call or empty response could not fit in a larger token allowance | `False` | `True` |
| `provider_error` | A provider call failed | `False` | `False` |
| `error` | Another exception stopped the loop | `False` | `False` |
| `cancelled` | A cancellation such as `KeyboardInterrupt` stopped the loop | `False` | `False` |

The first three statuses return normally. The other statuses describe runs that raised;
the exception carries the summary as `.summary` when it is an `AgentLoopError`.

## `ConversationSummary`

A frozen, JSON-serializable record produced once for every conversation. Its fields are:

| Fields | Meaning |
|---|---|
| `conversation_id`, `correlation_id`, `parent_id` | Conversation ID and optional caller-supplied grouping IDs |
| `agent_name`, `provider`, `model` | Agent and model identity |
| `status`, `iterations`, `iteration_limit`, `deadline_seconds`, `elapsed_seconds` | Outcome, model calls attempted, configured limits, and elapsed time |
| `tool_calls`, `tool_errors`, `provider_retries`, `usage` | Tool counts, extra provider attempts, and aggregated token usage and cost |
| `model_seconds`, `tool_seconds`, `retry_wait_seconds`, `tool_duration_seconds` | Time spent on model calls, tool batches, retry waits, and each tool's calls |
| `max_repeated_tool_calls`, `stalled` | Repeated identical calls and whether the configured stall threshold was reached |
| `timeline` | Optional tuple of `TurnRecord` entries; `None` unless enabled |
| `error_type`, `provider_status_code`, `error_phase`, `error_request_sent`, `error_request_id`, `error_attempts_sent`, `failed_attempts` | Details of a failed run and grouped failed provider attempts |

`cache_hit_rate` is a computed field: the share of input tokens served from cache, or
`None` when the provider did not report cache reads. The `succeeded` property reflects
`status.succeeded`.

`model_seconds` includes retry waits, which are also reported separately in
`retry_wait_seconds`. Tools may run concurrently, so the sum of their individual durations
in `tool_duration_seconds` can exceed the wall time in `tool_seconds`.

`None` means that a value was not reported or configured. It does not mean zero. For
example, `usage.cost_usd` is `None` when the provider did not report cost.

## `ConversationResult`

A frozen object containing `.transcript` and `.summary`. Its `.text` property returns the
final assistant text.

`Agent.run_result`, `Agent.converse_result`, and `run_conversation_result` return this type.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Inspect the answer and summary after a run:

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[]) as agent:
    result = agent.run_result("How many days are in a week? Reply with one digit.")

print(result.text)
print(result.summary.status.value)
print(result.summary.succeeded)
print(result.summary.iterations)
```

Output:

```text
7
completed
True
1
```

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
  as `(0.5, 0.8)`. Each fraction must be greater than 0 and at most 1. Duplicate values
  are removed and the values are sorted.

These options report conditions but do not stop or otherwise change the conversation.

## `TurnRecord`

Metadata for one model call. `index` is its one-based iteration number;
`model_seconds` and `tool_seconds` measure the call and its tool work. `stop_reason`,
`input_tokens`, `output_tokens`, and `cost_usd` describe the model response.

`attempts` counts provider attempts, and `attempt_log` holds one `AttemptRecord` per
attempt when retries occurred or the call failed. It is empty when the first attempt
succeeded. `tools` and `tool_errors` list tool names. `discarded` marks a truncated response
that the loop did not add to the transcript. When a call fails before returning,
`error_type` contains the exception class name and `input_tokens` / `output_tokens` are
`None` because no usage was reported.

## `FailureCount`

A `count` of failed provider attempts sharing one `error_type`, `phase` and `request_sent`.
`ConversationSummary.failed_attempts` holds one per combination, covering retried and final
failures alike. `ConversationSummary.error_phase`, `error_request_sent`, `error_request_id`
and `error_attempts_sent` describe the call that ended a failed run; `error_attempts_sent` is
the number of attempts that may have reached the provider, and `None` when no attempt log was
recorded.

## `ConversationContext`

The values passed to an observer when a conversation starts: `conversation_id`,
`correlation_id`, `parent_id`, `agent_name`, `provider`, `model`, `iteration_limit`, and
`deadline_seconds`.

See [Conversation summaries](../guides/summaries.md) for examples and operational guidance.
