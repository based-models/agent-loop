# Conversation summaries

Every run produces a `ConversationSummary` that explains how the conversation ended without
copying its content. It contains counts, names, status values, timing, and usage. It does not
contain messages, tool arguments, tool results, or exception text. This metadata-only format
is suitable for operational logs, metrics, and checkpoints, subject to your organization's
rules for identifiers and tool names.

Start with `Agent.run_result()`. It returns a `ConversationResult` whose `.summary` is the
`ConversationSummary`. With OpenAI configured as in
[Hello World](../getting-started/hello-world.md):

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[]) as agent:
    result = agent.run_result("Reply with exactly ready.", correlation_id="example-1")

print(result.text)
print(result.summary.model_dump_json(include={
    "correlation_id", "provider", "model", "status", "iterations",
    "tool_calls", "provider_retries", "usage",
}))
```

Output:

```text
ready
{"correlation_id":"example-1","provider":"openai","model":"gpt-5.1-2025-11-13","status":"completed","iterations":1,"tool_calls":{},"provider_retries":0,"usage":{"input_tokens":11,"output_tokens":11,"cache_read_tokens":0,"cache_write_tokens":null,"reasoning_tokens":0,"cost_usd":null,"input_includes_cached":true}}
```

`model_dump_json(include=...)` selects fields for display. The full summary also includes
the generated `conversation_id`, elapsed time, limits, and other counters.

## Status

Branch on `summary.status` (a `ConversationStatus`), never on an exception message.

| Status | Meaning | Returned or raised |
|---|---|---|
| `completed` | The model stopped asking for tools | returned |
| `refused` | The model declined | returned |
| `truncated` | The final answer hit `max_tokens` | returned |
| `iteration_limit` | Out of iterations | `LoopBudgetExceeded` |
| `deadline_exceeded` | Out of wall-clock time | `LoopBudgetExceeded` |
| `token_limit` | A turn cannot fit in `max_tokens` and the budget cannot grow | `LoopBudgetExceeded` |
| `provider_error` | A `ProviderError`, after any retries | raised |
| `error` | Anything else raised | raised |
| `cancelled` | `KeyboardInterrupt` and similar | raised |

`succeeded` and `budget_exhausted` are Boolean properties of `ConversationStatus`, not
additional statuses. `status.succeeded` is true for `completed`, `refused`, and `truncated`;
`status.budget_exhausted` is true for `iteration_limit`, `deadline_exceeded`, and
`token_limit`. Both are false for the other statuses.
`LoopBudgetExceeded` also has structured `.kind`, `.limit` and `.actual`.

## Get the summary

1. **From the result**: `run_result`, `converse_result` or `run_conversation_result` return
   a `ConversationResult` with `.transcript`, `.summary` and `.text`. The existing
   `run`, `converse` and `run_conversation` still return what they always did.
2. **From the exception**: an `AgentLoopError` raised by a run carries `.summary`, so a
   failure handler does not need an observer. Other exception types are reported only to
   observers.
3. **From an observer**: `on_conversation_end(summary)` fires exactly once per
   conversation, on success and failure. If the callback raises, the library logs that
   callback failure without hiding the conversation's result or error.

An observer receives a summary even when a run raises. `NullObserver` supplies no-op methods,
so a subclass can implement only the callback it needs. This `SummaryObserver` keeps the
latest summary:

```python
import os

from agentloop import Agent, ConversationSummary, NullObserver


class SummaryObserver(NullObserver):
    def __init__(self) -> None:
        self.latest: ConversationSummary | None = None

    def on_conversation_end(self, summary: ConversationSummary) -> None:
        self.latest = summary


observer = SummaryObserver()
with Agent.from_env(os.environ, tools=[], observer=observer) as agent:
    result = agent.run_result("Reply with exactly ready.")

print("observer received summary:", observer.latest == result.summary)
print("status:", result.summary.status)
```

Output:

```text
observer received summary: True
status: completed
```

The callback runs before `run_result()` returns. You can log
`summary.model_dump_json()` there to record every run, including failures.

## Grouping conversations

Your application supplies `correlation_id` and `parent_id`. For example, pass the same
`correlation_id` to every conversation used to handle one web request. The library copies
these values into summaries and OpenTelemetry root spans. It generates `conversation_id`
unless you supply one. Your application decides how to group, store, and aggregate runs.

## What the numbers mean

- `iterations` counts model calls attempted, including a call that raised, a discarded
  truncated turn and each paused continuation.
- `None` means unknown, never zero. `usage.cost_usd` is `None` unless a provider turn
  reported a cost; if only some turns did, it is the sum of those.
- `error_request_sent` and each `failed_attempts` entry's `request_sent` keep three states.
  `None` means the adapter could not tell, which is not the same as `False`.
- `provider_retries` counts attempts after the first, including attempts for a request that
  ultimately failed. It works with any `RetryingClient` and requires no observer setup.

## OpenTelemetry

`OTelObserver` copies the summary onto the root span as `agentloop.conversation.*`
attributes (status, iterations, iteration limit, elapsed time, ids, retries) and
`agentloop.tool.calls.<name>` / `agentloop.tool.errors.<name>` counts. These are the same
whether or not `capture_content` is on.

## Where the time went

`model_seconds` is time inside model calls (retries and their waits included),
`retry_wait_seconds` is the part of that spent sleeping between attempts, and
`tool_seconds` is wall time spent running tool batches. `tool_duration_seconds` sums each
tool's own call durations; tools in one turn run in parallel, so it can exceed
`tool_seconds`.

`cache_hit_rate` is the share of prompt tokens served from cache, or `None` when the
provider reported no cache tokens. It accounts for providers that count cached tokens inside
`input_tokens` (OpenAI-style) and those that report them separately (Anthropic).

## Optional detail and warnings

`SummaryOptions` enables extra reporting. Pass it to `Agent` when you want a timeline, stall
detection, or budget warnings:

```python
import os

from agentloop import Agent, SummaryOptions

with Agent.from_env(
    os.environ,
    tools=[],
    summary_options=SummaryOptions(timeline=True, stall_threshold=3, warn_at=(0.8,)),
) as agent:
    result = agent.run_result("Reply with exactly ready.")

print("timeline entries:", len(result.summary.timeline or ()))
print("first stop reason:", result.summary.timeline[0].stop_reason)
```

Output:

```text
timeline entries: 1
first stop reason: end_turn
```

These options are off by default.

- **`stall_threshold`**: flag a run in which the same tool was called with identical arguments
  that many times (3 is a good start). `max_repeated_tool_calls` is reported either way;
  `stalled` and the `on_stall` event need the threshold. The flag never changes behavior or
  status: a stalled run that hits its limit is still `iteration_limit`, with `stalled=True`
  explaining why. Calls are compared by hash and only counts are kept, so neither the
  arguments nor the hash reach a summary, event or metric.
- **`warn_at`**: fractions of the iteration limit and the deadline (for example `(0.5, 0.8)`)
  at which `on_budget_warning(kind, used, limit)` fires, once each.
- **`timeline`**: `summary.timeline` becomes a tuple of `TurnRecord`s, one per model call:
  duration, stop reason, tokens, cost, attempts, the tools it led to and which of them
  errored, and whether a truncated turn was discarded. A call that raised appears with its
  `error_type` and `None` tokens because the call never answered. The timeline also excludes
  conversation content.

## How reporting stays separate from execution

The loop reports iterations, limits, elapsed time, status, and identifiers.
`SummaryCollector` gathers tokens, tool counts, and retries through the same observer
interface available to applications. JSONL event traces, metrics, and distributed traces are
separate observers, so reporting failures do not change agent behavior.
