# API reference

This reference covers the main public interfaces. Start with the
[top-level package](agentloop.md) for common imports, or use the lower-level modules
when you need direct control of clients, tools, observers, or conversation results.

| Module | Purpose |
|---|---|
| [`agentloop`](agentloop.md) | Ergonomic re-exports |
| [`agentloop.agent`](agent.md) | High-level agent and tool decorator |
| [`agentloop.collector`](../guides/summaries.md) | `SummaryCollector`, the loop's per-conversation usage and tool counter |
| [`agentloop.config`](config.md) | Settings and client construction |
| [`agentloop.errors`](errors.md) | Exception hierarchy |
| [`agentloop.loop`](loop.md) | Core conversation loop |
| [`agentloop.observer`](observer.md) | Lifecycle callbacks |
| [`agentloop.summary`](summary.md) | Conversation outcomes, usage, timing, and limits |
| [`agentloop.events`](events.md) | Ordered JSONL event traces |
| [`agentloop.metrics`](metrics.md) | OpenTelemetry counters and histograms |
| [`agentloop.provider`](provider.md) | Shared request, response, and client types |
| [`agentloop.retry`](retry.md) | Retry policy and wrapper |
| [`agentloop.stall`](summary.md#summaryoptions) | `RepeatTracker`, used for `SummaryOptions.stall_threshold` reporting |
| [`agentloop.tooling`](tooling.md) | Registry and tool execution |
| [`agentloop.tracing`](tracing.md) | OpenTelemetry observer and setup |
| [`agentloop.transcript`](transcript.md) | Conversation messages and parts |
| [`agentloop.providers`](providers/index.md) | Built-in provider integrations |

The [provider index](providers/index.md) compares the built-in adapters and links to
their constructors. The [custom providers guide](../guides/custom-providers.md) explains
how to implement `LLMClient`.

Pydantic models in this package validate construction. Models described as frozen reject
attribute assignment after construction.
