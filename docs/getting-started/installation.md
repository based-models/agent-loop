# Installation

`based-models-agentloop` requires Python 3.12 or newer. Install the distribution named
`based-models-agentloop`; import it in Python as `agentloop`.

For the core agent loop and its offline `FakeClient`, no provider extra is needed:

```bash
python -m pip install based-models-agentloop
```

Choose an extra when you connect to a provider or export telemetry:

| Extra | Adds |
|---|---|
| `anthropic` | Anthropic SDK |
| `openai-compat` | HTTP client for OpenAI, OpenRouter, and Modal/vLLM |
| `otel` | OpenTelemetry SDK and OTLP HTTP exporter |
| `all` | All three extras above |

For example, install **one** provider extra with either of these commands:

```bash
python -m pip install 'based-models-agentloop[anthropic]'
python -m pip install 'based-models-agentloop[openai-compat]'
```

Add the `otel` extra if the application exports spans or metrics. Install every integration
with `all`:

```bash
python -m pip install 'based-models-agentloop[otel]'
python -m pip install 'based-models-agentloop[all]'
```

Check that the package imports in the selected Python environment:

```bash
python -c 'import agentloop; print("agentloop ready")'
```

Output:

```text
agentloop ready
```

## Work from this repository

With Python 3.12 or newer available as `python`, create a virtual environment and install
the project with its development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[all,dev]'
```

Provider SDKs and the OpenTelemetry SDK are imported only when their integrations are
used. A basic `import agentloop` works without those optional extras.

Next: [Hello World](hello-world.md).
