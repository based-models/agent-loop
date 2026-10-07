# API reference

The reference documents the supported, non-underscore-prefixed API. Most users start with
the [top-level package](agentloop.md); extension authors use the lower-level modules.

| Module | Purpose |
|---|---|
| [`agentloop`](agentloop.md) | Ergonomic re-exports |
| [`agentloop.agent`](agent.md) | High-level agent and tool decorator |
| [`agentloop.config`](config.md) | Settings and client construction |
| [`agentloop.errors`](errors.md) | Exception hierarchy |
| [`agentloop.loop`](loop.md) | Core conversation loop |
| [`agentloop.observer`](observer.md) | Lifecycle callbacks |
| [`agentloop.summary`](summary.md) | Conversation outcomes, usage, timing, and limits |
| [`agentloop.events`](events.md) | Ordered JSONL event traces |
| [`agentloop.metrics`](metrics.md) | OpenTelemetry counters and histograms |
| [`agentloop.provider`](provider.md) | Shared request, response, and client types |
| [`agentloop.retry`](retry.md) | Retry policy and wrapper |
| [`agentloop.tooling`](tooling.md) | Registry and tool execution |
| [`agentloop.tracing`](tracing.md) | OpenTelemetry observer and setup |
| [`agentloop.transcript`](transcript.md) | Conversation messages and parts |
| [`agentloop.providers`](providers/index.md) | Built-in provider integrations |

Pydantic models in this package validate construction. Models described as frozen reject
attribute assignment after construction.
