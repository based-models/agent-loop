# Installation

agentloop requires Python 3.12 or newer. Install the extra for the provider you use:

```bash
pip install 'agentloop[anthropic]'
pip install 'agentloop[openai-compat]'
pip install 'agentloop[otel]'
```

`openai-compat` supports OpenAI, OpenRouter, and Modal. `otel` adds the OpenTelemetry SDK
and OTLP HTTP exporter. Install everything for development or experimentation with:

```bash
pip install 'agentloop[all]'
```

For a checkout of this repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[all,dev]'
```

No provider SDK is imported until its client is constructed. A basic `import agentloop`
therefore does not require every optional dependency.

Next: [Hello World](hello-world.md).
