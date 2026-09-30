# Hello World across providers

The program is identical for every supported provider. Configuration chooses the adapter.

```python
import os

from agentloop import Agent

with Agent.from_env(os.environ, tools=[]) as agent:
    print(agent.run("Reply with exactly: Hello, world!"))
```

Save it as `hello.py`, configure one provider, and run `python hello.py`.

## Anthropic

```bash
pip install 'based-models-agentloop[anthropic]'
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY='...'
export ANTHROPIC_MODEL='claude-opus-5'  # optional
python hello.py
```

When `ANTHROPIC_API_KEY` is omitted, the Anthropic SDK may use another credential source it
supports, such as an authenticated CLI profile.

## OpenAI

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='...'
export OPENAI_MODEL='gpt-5.1'  # optional
python hello.py
```

## OpenRouter

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openrouter
export OPENROUTER_API_KEY='...'
export OPENROUTER_MODEL='openrouter/free'  # optional
python hello.py
```

## Modal

```bash
pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=modal
export MODAL_ENDPOINT_URL='https://example--model.modal.run'
export MODAL_ENDPOINT_MODEL='Qwen/Qwen3.6-35B-A3B-FP8'  # optional
python hello.py
```

For an authenticated Modal proxy, set both `MODAL_PROXY_TOKEN_ID` and
`MODAL_PROXY_TOKEN_SECRET`. Setting only one is a configuration error.

Do not commit credentials. `Agent.from_env` receives a mapping explicitly; it does not load
`.env` files or inspect the process environment on its own.
