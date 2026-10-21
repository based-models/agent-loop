# Examples

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. Start with a single
model call, then add a local tool or observe the agent loop. The first four examples use
OpenAI; the custom provider example runs locally without a model service.

- [Minimal agent](minimal-agent.md): ask one question and print the answer.
- [Tool-enabled agent](tool-enabled-agent.md): let the model call a local Python function.
- [Custom observer](custom-observer.md): count tokens and record JSONL events.
- [Traced agent](traced-agent.md): inspect conversation and model spans in memory.
- [Custom provider](custom-provider.md): implement a client that returns a fixed response.
