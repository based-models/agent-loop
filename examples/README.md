# examples

Complete agents built **on** `agentloop`, as a consumer would build them.

Nothing here ships in the wheel — these sit beside `src/`, not inside it, and the src
layout excludes them automatically. Chapter 13's packaging test asserts that. Shipping a
consumer of the package inside the package would be circular.

They are, however, type-checked and linted as strictly as the library (`mypy` `files`
includes `examples`), and chapter 12 asserts they only use the public API — no reaching
around `build_client` into `agentloop.providers`. A broken example is a broken advertisement.

| File | Chapter | What it shows |
|---|---|---|
| `datetime_agent.py` | 09 | The smallest complete agent, written out explicitly: deps bundle, tool, observer, wiring. The same toy tool Anthropic's notebook opens with — and the proof the loop is use-case agnostic, because the script brings its own deps bundle. |
| `datetime_agent_terse.py` | 10 | The same agent through `Agent` and `@tool`. 63 lines against 146, and the transcripts match. |
| `traced_agent.py` | 11 | The same agent again, with OpenTelemetry. Owns the SDK setup, because configuring a process-global tracer is an application's job and never a library's. |

Run them with the environment loaded explicitly. There is no `python-dotenv` in this
repository and there will not be — nothing here reads a file to find its configuration:

```bash
set -a; source .env; set +a
LLM_PROVIDER=anthropic python examples/datetime_agent.py
```

`traced_agent.py` additionally wants an OTLP endpoint, and degrades to printing
`tracing to (nowhere)` without one:

```bash
docker run -d --name otel -p 4318:4318 -p 16686:16686 jaegertracing/all-in-one:latest
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 python examples/traced_agent.py
```
