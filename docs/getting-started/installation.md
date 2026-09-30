# Installation

based-models-agentloop requires Python 3.12 or newer. The import name is `agentloop`.
Install the extra for the provider you use:

```bash
pip install 'based-models-agentloop[anthropic]'
pip install 'based-models-agentloop[openai-compat]'
pip install 'based-models-agentloop[otel]'
```

`openai-compat` supports OpenAI, OpenRouter, and Modal. `otel` adds the OpenTelemetry SDK
and OTLP HTTP exporter. Install everything with:

```bash
pip install 'based-models-agentloop[all]'
```

For a checkout of this repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[all,dev]'
```

No provider SDK is imported until its client is constructed, so a basic `import agentloop`
does not require every optional dependency.

Next: [Hello World](hello-world.md).
