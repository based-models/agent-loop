# `agentloop.metrics`

OpenTelemetry metrics for dashboards and alerts.

## `MetricsObserver`

```text
MetricsObserver(meter=None)
```

Records counters and histograms when a conversation ends. Pass an OpenTelemetry `Meter`, or
let the observer use `opentelemetry.metrics.get_meter("agentloop")`. The observer records
nothing until the application installs a `MeterProvider`.

`MetricsObserver` keeps no per-conversation state and can be shared by concurrent
conversations. Use `Agent(metrics=True)` to add one automatically.

Metric attributes are limited to values such as status, agent name, provider, model, tool,
and error state. Conversation, correlation, and parent IDs are intentionally excluded because
unique values would create a separate metric series for every conversation. Use a trace or
event trace to inspect a specific conversation.

See [OpenTelemetry tracing and metrics](../guides/tracing.md#metrics) for the full instrument
list.
