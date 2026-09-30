# Installation

based-models-agentloop requires Python 3.12 or newer and is a **private package**: it is
installed from its git repository, never from a package index. The import name is
`agentloop`. See [Installing](../guides/installing.md) for the reasoning, pinning and CI
setup. In short:

```bash
pip install 'based-models-agentloop[anthropic] @ git+ssh://git@github.com/based-models/agent-loop.git@<sha>'
```

Extras: `anthropic`, `openai-compat` (OpenAI, OpenRouter, Modal), `otel` (OpenTelemetry SDK
and OTLP HTTP exporter), and `all`.

For a checkout of this repository:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[all,dev]'
```

No provider SDK is imported until its client is constructed, so a basic `import agentloop`
does not require every optional dependency.

Next: [Hello World](hello-world.md).
