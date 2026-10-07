# Conversation summaries

When an agent stops, you want to know why without reading its prompts. Every run produces a
`ConversationSummary`: counts, names and statuses only. No message, tool argument, tool
result or exception text is ever in it, so it is safe to log, ship to a metrics backend or
persist with a checkpoint.

```python
result = agent.run_result("find the lead investor", correlation_id="round-42")
print(result.text)
print(result.summary.model_dump_json())
```

```json
{
  "conversation_id": "9f1c...",
  "correlation_id": "round-42",
  "agent_name": "investor-lookup",
  "provider": "openrouter",
  "model": "gpt-5.1",
  "status": "iteration_limit",
  "iterations": 30,
  "iteration_limit": 30,
  "elapsed_seconds": 412.8,
  "tool_calls": {"web_search": 4, "fetch_source": 8},
  "tool_errors": {"fetch_source": 2},
  "provider_retries": 1,
  "usage": {"input_tokens": 12000, "output_tokens": 3500, "cost_usd": 0.08}
}
```

## Status

Branch on `summary.status` (a `ConversationStatus`), never on an exception message.

| Status | Meaning | Returned or raised |
|---|---|---|
| `completed` | The model stopped asking for tools | returned |
| `refused` | The model declined | returned |
| `truncated` | The final answer hit `max_tokens` | returned |
| `iteration_limit` | Out of iterations | `LoopBudgetExceeded` |
| `deadline_exceeded` | Out of wall-clock time | `LoopBudgetExceeded` |
| `provider_error` | A `ProviderError`, after any retries | raised |
| `error` | Anything else raised | raised |
| `cancelled` | `KeyboardInterrupt` and similar | raised |

`status.succeeded` and `status.budget_exhausted` cover the common branches.
`LoopBudgetExceeded` also has structured `.kind`, `.limit` and `.actual`.

## Three ways to receive it

1. **From the result**: `run_result`, `converse_result` or `run_conversation_result` return
   a `ConversationResult` with `.transcript`, `.summary` and `.text`. The existing
   `run`, `converse` and `run_conversation` still return what they always did.
2. **From the exception**: an `AgentLoopError` raised out of a run carries `.summary`, so a
   failure handler needs no observer. Exceptions that are not `AgentLoopError` reach only
   observers.
3. **From an observer**: `on_conversation_end(summary)` fires exactly once per
   conversation, on success and failure, while `conversation()` is still open (so a tracer
   can write to its span). A sink that raises is logged and cannot mask the real failure.

```python
class JsonlSink(NullObserver):
    def on_conversation_end(self, summary: ConversationSummary) -> None:
        log.info(summary.model_dump_json())
```

## Grouping conversations

`correlation_id` and `parent_id` are yours: pass the same `correlation_id` to every
conversation in one run (a funding round, a web request) and the library echoes it in each
summary and on the OpenTelemetry root span. `conversation_id` is generated unless you pass
one. Grouping, aggregation and persistence across conversations belong to your orchestration
layer; the library only labels.

## What the numbers mean

- `iterations` counts model calls attempted, including a call that raised, a discarded
  truncated turn and each paused continuation.
- `None` means unknown, never zero. `usage.cost_usd` is `None` unless a provider turn
  reported a cost; if only some turns did, it is the sum of those.
- `provider_retries` counts extra attempts beyond the first, across all calls, including a
  call that finally failed. It works with any `RetryingClient`, with no observer wiring.

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

## Opt-in extras: `SummaryOptions`

```python
from agentloop import Agent, SummaryOptions

agent = Agent(
    tools,
    client=client,
    summary_options=SummaryOptions(timeline=True, stall_threshold=3, warn_at=(0.8,)),
)
```

Everything is off by default; one object holds the knobs so entry points do not grow a
parameter per feature.

- **`stall_threshold`**: flag a run in which one tool was called with identical arguments
  that many times (3 is a good start). `max_repeated_tool_calls` is reported either way;
  `stalled` and the `on_stall` event need the threshold. The flag never changes behavior or
  status: a stalled run that hits its limit is still `iteration_limit`, with `stalled=True`
  explaining why. Calls are compared by hash and only counts are kept, so neither the
  arguments nor the hash reach a summary, event or metric.
- **`warn_at`**: fractions of the iteration limit and the deadline (for example `(0.5, 0.8)`)
  at which `on_budget_warning(kind, used, limit)` fires, once each, so something can
  intervene before the failure.
- **`timeline`**: `summary.timeline` becomes a tuple of `TurnRecord`s, one per model call:
  duration, stop reason, tokens, cost, attempts, the tools it led to and which of them
  errored, and whether a truncated turn was discarded. A call that raised appears with its
  `error_type`. Still content-free.

## Seam

The loop supplies only what it alone knows: iterations, limits, elapsed time, status and
ids. `SummaryCollector`, an ordinary observer, aggregates tokens, tool counts and retries.
Sinks (JSONL, metrics, tracing) are further observers. Nothing here imports OpenTelemetry
or touches the machine.
