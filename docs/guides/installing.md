# Installing

```bash
pip install 'based-models-agentloop[anthropic]'
```

The distribution name is `based-models-agentloop`; the import name is `agentloop`. (The
bare name `agentloop` on PyPI is an unrelated project. Do not install it by mistake.)

## Extras

`anthropic`, `openai-compat` (OpenAI, OpenRouter, Modal), `otel` (OpenTelemetry SDK and OTLP
exporter) and `all`. Extras are genuinely optional: a missing one fails with an error that
names the extra to install.

## Pinning

Pin a compatible range in your own project. While the version is `0.x`, a minor release can
change the public API:

```text
based-models-agentloop[anthropic]>=0.4,<0.5
```

For reproducible and tamper-evident installs, compile a hashed lockfile
(`pip-compile --generate-hashes`) and install with `pip install --require-hashes`.

## Verifying a release

Each PyPI release is published by GitHub Actions through Trusted Publishing and carries
build attestations. The same wheel, sdist and `SHA256SUMS` are attached to the matching
GitHub release.
