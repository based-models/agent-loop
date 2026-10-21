# Hello World across providers

Start with a scripted client to see the agent loop run without an API key. `FakeClient`
returns the response you give it and records the request the loop sent.

```python
from agentloop import Agent
from agentloop.provider import ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.transcript import TextPart

client = FakeClient(
    ModelResponse(
        content=(TextPart(text="Hello, world!"),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=3, output_tokens=4),
        model="fake-1",
        provider="fake",
    )
)
agent = Agent([], client=client)
print(agent.run("Say hello."))
print(f"model requests: {len(client.requests)}")
```

Output:

```text
Hello, world!
model requests: 1
```

To call a live provider, replace the `FakeClient` and `Agent` construction above with
`Agent.from_env`. The same `run()` call works for every built-in provider:

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[]) as agent:
    print(agent.run("Say hello."))
```

Save the live example as `hello.py`, choose one configuration below, and run
`python hello.py`. The reply comes from the selected model, so its exact text can vary.
`Agent.from_env` receives `os.environ` explicitly; the library does not read the process
environment or a `.env` file on its own.

## Anthropic

```bash
pip install 'based-models-agentloop[anthropic]'
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY='your-key'
python hello.py
```

When `ANTHROPIC_API_KEY` is omitted, the Anthropic SDK may use another credential source it
supports, such as an authenticated CLI profile. The default model is `claude-opus-5`; set
`ANTHROPIC_MODEL` to choose another.

## OpenAI

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
python hello.py
```

OpenAI uses the Responses API and model `gpt-5.1` by default. Set `OPENAI_MODEL` to choose
another model or `OPENAI_API=completions` for Chat Completions compatibility.

## OpenRouter

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openrouter
export OPENROUTER_API_KEY='your-key'
python hello.py
```

The default model is `openrouter/free`; set `OPENROUTER_MODEL` to choose another.

## Modal

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=modal
export MODAL_ENDPOINT_URL='https://your-deployment.modal.run'
python hello.py
```

The default endpoint model is `Qwen/Qwen3.6-35B-A3B-FP8`; set `MODAL_ENDPOINT_MODEL` to
match your deployment. For an authenticated Modal proxy, set both `MODAL_PROXY_TOKEN_ID`
and `MODAL_PROXY_TOKEN_SECRET`. Setting only one is a configuration error.

Keep credentials out of source control. See [configuration](configuration.md) for the full
list of settings.
