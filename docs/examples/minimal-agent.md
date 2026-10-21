# Minimal agent

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses OpenAI.

Install OpenAI support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Create an agent and ask one question:

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[], system="Answer briefly.") as agent:
    print(agent.run("What is 2 + 2? Answer with one digit."))
```

Output:

```text
4
```

`Agent.from_env()` uses the environment mapping you pass it. `run()` starts a new
conversation and returns the final text. The `with` block closes the client when the run
finishes. See [Agents](../guides/agents.md) to continue a conversation or add tools.
