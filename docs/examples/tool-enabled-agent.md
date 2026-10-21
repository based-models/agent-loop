# Tool-enabled agent

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses OpenAI.

Install OpenAI support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

Give the agent a local function it can call:

```python
import os

from agentloop import Agent, tool

@tool(parameters={
    "type": "object",
    "properties": {"word": {"type": "string"}},
    "required": ["word"],
    "additionalProperties": False,
})
def count_letters(word: str) -> str:
    """Count the letters in a word."""
    return str(len(word))

with Agent.from_env(
    os.environ,
    tools=[count_letters],
    system="Use count_letters to count letters before answering.",
) as agent:
    result = agent.run_result(
        "How many letters are in agent? Use count_letters. Reply with just the number."
    )
    print(result.text)
    print(result.summary.tool_calls)
```

Output:

```text
5
{'count_letters': 1}
```

The schema tells the model what argument to pass. When it calls `count_letters`, the agent
executes the Python function and sends its result back to the model. `run_result()` returns
the final answer and a summary; `tool_calls` confirms that the tool ran once.
