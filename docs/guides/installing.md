# Installing

`based-models-agentloop` requires Python 3.12 or newer. Install the distribution named
`based-models-agentloop` and import it as `agentloop`.

For OpenAI, install the `openai-compat` extra:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
```

Check the import in the same Python environment:

```bash
python -c 'import agentloop; print("agentloop ready")'
```

Output:

```text
agentloop ready
```

## Extras

| Extra | Adds |
|---|---|
| `anthropic` | Anthropic SDK |
| `openai-compat` | HTTP client for OpenAI, OpenRouter, and Modal/vLLM |
| `otel` | OpenTelemetry SDK and OTLP HTTP exporter |
| `all` | All three integrations above |

The core package does not require these integrations. If you use a provider or telemetry
feature without its dependency, the error names the extra to install. See the
[getting-started installation guide](../getting-started/installation.md) for more install
commands and local development setup.

## Pinning

Pin a compatible range in your project. While the version is `0.x`, a minor release can
change the public API. For example, to stay on the `0.5` series with OpenAI support, use
this requirement in your project's dependency file:

```text
based-models-agentloop[openai-compat]>=0.5,<0.6
```

For repeatable deployments, lock the selected version and its dependencies. If your project
uses `pip-tools`, `pip-compile --generate-hashes` can produce a hashed requirements file for
`pip install --require-hashes`.

## Verifying a release

The [PyPI files page](https://pypi.org/project/based-models-agentloop/#files) shows
the wheel, source distribution, checksums, and provenance for the current version. The
matching [GitHub release](https://github.com/based-models/agent-loop/releases/latest)
carries the wheel, source distribution, and `SHA256SUMS`.
