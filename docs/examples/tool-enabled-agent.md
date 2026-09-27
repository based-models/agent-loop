# Tool-enabled agent

```python
import os

from agentloop import Agent, ConsoleObserver, tool

WEATHER_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "description": "City name"},
    },
    "required": ["city"],
    "additionalProperties": False,
}

@tool(parameters=WEATHER_SCHEMA)
def weather(city: str) -> str:
    """Return example weather for a city."""
    return f"The example forecast for {city} is sunny."

with Agent.from_env(
    os.environ,
    tools=[weather],
    observer=ConsoleObserver(),
    system="Use the weather tool for forecasts.",
) as agent:
    agent.run("What is the weather in Lisbon?")
```

`ConsoleObserver` prints the tool call, result, and final response, so the return value from
`run()` is intentionally not printed again.
