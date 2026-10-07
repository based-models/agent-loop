# OpenTelemetry tracing

Install the tracing extra, configure an exporter, and enable tracing on the agent:

```bash
pip install 'based-models-agentloop[otel]'
```

```python
import os

from agentloop import Agent
from agentloop.tracing import configure_tracing

provider = configure_tracing(
    "http://localhost:4318",
    service_name="hello-agent",
)
try:
    with Agent.from_env(
        os.environ,
        tools=[],
        name="hello-agent",
        trace=True,
    ) as agent:
        print(agent.run("Hello"))
    provider.force_flush()
finally:
    provider.shutdown()
```

Tracing creates one conversation root span, a child for each model request, and a sibling
for each tool call. Retries are events on the active model span.

Prompt, tool, result, and provider-error content is not captured by default. Construct an
`OTelObserver(..., capture_content=True)` only when your data-handling policy permits that
content to leave the process. One `OTelObserver` can trace only one conversation at a time.

The root span also carries the conversation's outcome (`agentloop.conversation.*`: status,
iterations against the limit, time split, ids, stall flag) and `agentloop.usage.cache_hit_rate`
when known, plus `agentloop.stall` and `agentloop.budget_warning` events. These are the
same whether or not content capture is on.

## Metrics

For dashboards and alerts, `agentloop.metrics.MetricsObserver` (or `Agent(metrics=True)`)
records counters and histograms through the OpenTelemetry metrics API. Like tracing, it
records nothing until your application installs a `MeterProvider`.

| Instrument | Kind | Attributes |
|---|---|---|
| `agentloop.conversations` | counter | status, agent_name, provider, model |
| `agentloop.conversation.duration`, `.iterations` | histogram | same |
| `agentloop.conversation.model_duration` | histogram | agent_name, provider, model |
| `agentloop.conversation.tool_duration` | histogram, per tool per conversation | + tool |
| `agentloop.tool.calls` | counter | tool, error |
| `agentloop.tokens` | counter | provider, model, type |
| `agentloop.cost` | counter (USD), only when a provider reports one | provider, model |
| `agentloop.provider.retries` | counter | provider, model |
| `agentloop.stalls`, `agentloop.budget_warnings` | counters | tool / kind |

Attributes are low-cardinality on purpose. Conversation, correlation and parent ids are
never metric attributes: a unique value per run would give each run its own time series.
Use traces or the event trace to find a specific run.

See the [`tracing` API](../api/tracing.md).
