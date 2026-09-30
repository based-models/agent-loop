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

See the [`tracing` API](../api/tracing.md).
