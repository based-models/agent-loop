# Minimal agent

```python
import os

from agentloop import Agent

def main() -> None:
    with Agent.from_env(
        os.environ,
        tools=[],
        system="Answer in one sentence.",
    ) as agent:
        print(agent.run("Why is the sky blue?"))

if __name__ == "__main__":
    main()
```

Set one of the provider configurations from [Hello World](../getting-started/hello-world.md)
before running the program.
