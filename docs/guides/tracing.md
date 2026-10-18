# OpenTelemetry tracing and metrics

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

Tracing creates one top-level span for the conversation, one child span for each model
request, and one span for each local tool call. A span is one timed operation in an
OpenTelemetry trace. Retries appear as events on the related model-request span.

Prompts, tool arguments, tool results, and provider error messages are not captured by
default. Construct an
`OTelObserver(..., capture_content=True)` only when your data-handling policy permits that
content to leave the process. One `OTelObserver` can trace only one conversation at a time.

The conversation span records its status, identifiers, iterations, limits, timing, retries,
usage, and stall flag. A failed call also gets `error.type`, `agentloop.error.phase` and `agentloop.error.request_sent`
(omitted when unknown) on its model span and on the root span, and every model span records
`agentloop.attempts`. It also receives stall and limit-warning events. This metadata is the
same whether content capture is on or off.

## Metrics

For dashboards and alerts, `agentloop.metrics.MetricsObserver`—or the shorter
`Agent(metrics=True)` option—records counters and distributions through the OpenTelemetry
metrics API. Like tracing, it records nothing until your application configures an
OpenTelemetry `MeterProvider`.

```python
import os

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from agentloop import Agent

exporter = OTLPMetricExporter(endpoint="http://localhost:4318/v1/metrics")
reader = PeriodicExportingMetricReader(exporter)
meter_provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(meter_provider)

try:
    with Agent.from_env(os.environ, tools=[], metrics=True) as agent:
        agent.run("Hello")
    meter_provider.force_flush()
finally:
    meter_provider.shutdown()
```

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
| `agentloop.provider.errors` | counter | provider, model, error_type, phase, request_sent (`true`/`false`/`unknown`) |
| `agentloop.stalls`, `agentloop.budget_warnings` | counters | tool / kind |

Metric labels use a small, repeatable set of values such as status, provider, model, and tool
name. Conversation, correlation, and parent IDs are excluded because unique IDs would create
a separate metric series for every run and can overwhelm a metrics system. Use distributed
traces or an event trace to find a specific conversation.

See the [`tracing` API](../api/tracing.md).
