# Traced agent

```python
import os

from agentloop import Agent, ConsoleObserver
from agentloop.tracing import configure_tracing

endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
tracer_provider = configure_tracing(endpoint, service_name="example-agent")

try:
    with Agent.from_env(
        os.environ,
        tools=[],
        observer=ConsoleObserver(),
        name="example-agent",
        trace=True,
    ) as agent:
        agent.run("Hello")
    tracer_provider.force_flush()
finally:
    tracer_provider.shutdown()
```

The application explicitly reads the endpoint and owns the returned tracer provider's
lifecycle.
