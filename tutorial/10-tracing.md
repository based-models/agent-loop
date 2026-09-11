# 10 — Tracing with OpenTelemetry

Now that it runs, watch it. Divergence **D14**: the OpenTelemetry API directly, not Opik, not Logfire.

Builds `src/harness/tracing.py`.

---

## Why here and not earlier

Three reasons for this exact slot in the tutorial:

1. **You cannot meaningfully trace a loop that doesn't run.** Before chapter 9 there was nothing to look at.
2. **Retries must appear inside the model span.** Chapter 8's `on_retry` had to exist first, or you'd instrument retries twice.
3. **Retrofitting tracing is the honest test of whether `LoopObserver` is sufficient.** If the protocol were wrong, you'd find out here — at the one point where fixing it is cheap. It isn't wrong, and that's worth confirming rather than assuming.

---

## It is an observer. That's the whole design.

```python
class OTelObserver:
    """A LoopObserver that emits GenAI spans. loop.py never imports OpenTelemetry."""
```

`loop.py` has zero third-party imports, including this one. Chapter 11's AST test enforces it.

This is the concrete payoff of **D2** — replacing the reference course's `Boundary` async generator with a callback protocol. Chapter 7 shaped the protocol as three enter/exit pairs:

| Observer pair | Span |
|---|---|
| `conversation()` | root `invoke_agent` |
| `on_request` / `on_response` | `chat {model}` |
| `on_tool_start` / `on_tool_end` | `execute_tool {name}` |
| `on_retry` | an *event* on the current `chat` span |

A span is an enter/exit pair. The protocol is enter/exit pairs. Tracing is a drop-in, and the reference's `logfire.instrument_pydantic_ai()` has no equivalent here because there's nothing to monkeypatch — you own the seam.

---

## Three rules for a library

This is where most libraries get tracing wrong. Each rule below is a real failure, not a style note.

### 1. `opentelemetry-api` is the only hard dependency

```toml
dependencies = ["opentelemetry-api>=1.30"]

[project.optional-dependencies]
otel = ["opentelemetry-sdk>=1.30", "opentelemetry-exporter-otlp-proto-http>=1.30"]
```

The **API** package is a no-op when no SDK is configured. `trace.get_tracer(...)` returns a `ProxyTracer`, `start_as_current_span` returns a `NonRecordingSpan`, `set_attribute` does nothing, and nothing touches the network. A consumer who doesn't want tracing pays essentially zero.

The **SDK** and exporter — the parts that allocate, batch, and send — are an extra, installed by the CLI and by your tests.

This is why the library can call tracing functions unconditionally. There are no
`if tracing_enabled:` branches anywhere, and no separate "do nothing" implementation to
maintain — when no SDK is configured, the API package's own objects already do nothing.

### 2. Never call `set_tracer_provider()`. Never read `OTEL_*`.

```python
# src/harness/tracing.py
from opentelemetry import trace

_tracer = trace.get_tracer("harness")     # ✅ the only global interaction
```

```python
# ❌ never, in a library
trace.set_tracer_provider(TracerProvider())
os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
```

The tracer provider is **process-global, and set-once** — the OTel SDK logs a warning and ignores the second call. A library that sets it either loses (harmless, confusing) or wins (actively breaks the application's own tracing, dropping every span the app configured). Either way the consumer debugs your library instead of their app.

The reference course configures its exporter programmatically for a narrower version of this problem — it had to avoid clobbering an OpenTelemetry SDK belonging to another dependency. The general form: **the application owns global state; the library asks for what it needs.**

So setup lives in `cli.py`:

```python
def _configure_tracing(endpoint: str | None) -> None:
    """Called by the CLI — the application — never by the library."""
    if not endpoint:
        return
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": "harness"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
```

Same rule as chapter 1's `Settings`, applied to a different global.

### 3. Follow the semantic conventions

Don't invent attribute names. The GenAI conventions map almost exactly onto the types you already have — which is a good sign chapter 3's `ModelRequest` / `Usage` were cut correctly.

**They live in their own repository now** — [`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai), moved out of the main semconv repo — and **every attribute below is `Development` stability**, not Stable. They will change. Pin the version you targeted in a comment so the next person knows what you were reading:

```python
# GenAI semantic conventions: open-telemetry/semantic-conventions-genai,
# docs/gen-ai/gen-ai-spans.md. Status: Development — expect drift.
```

One rename that catches people: **`gen_ai.system` became `gen_ai.provider.name`.** Older blog posts and some instrumentation still use the old name.

---

## The spans

### Span names

The convention is `{gen_ai.operation.name} {gen_ai.request.model}`:

```
invoke_agent harness              (root — one per run_conversation)
├─ chat claude-opus-5
├─ execute_tool read
├─ chat claude-opus-5
│  └─ [event] retry
├─ execute_tool edit
├─ execute_tool bash
└─ chat claude-opus-5
```

Tool spans are siblings of `chat` spans, not children — the tool runs *after* the model request completed, so nesting them would misrepresent the timeline.

### The model span

```python
def on_request(self, request: ModelRequest) -> None:
    span = _tracer.start_span(
        f"chat {self._model}",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self._provider,     # NOT gen_ai.system
            "gen_ai.request.model": self._model,
            "gen_ai.request.max_tokens": request.max_tokens,
            "harness.request.message_count": len(request.messages),
            "harness.request.tool_count": len(request.tools),
        },
    )
    if request.temperature is not None:
        span.set_attribute("gen_ai.request.temperature", request.temperature)
    self._model_span = span
    self._model_ctx = trace.use_span(span, end_on_exit=False)
    self._model_ctx.__enter__()
```

Two notes.

**`temperature` is set only when present** — same rule as chapter 4's payload construction, for the same reason. An attribute asserting `temperature = 1.0` on a model that rejects the parameter is a lie in your telemetry.

**Custom attributes get a `harness.` prefix.** Never invent new `gen_ai.*` names — that namespace belongs to the spec, and squatting in it means your attribute silently conflicts when the convention adds the real one.

```python
def on_response(self, response: ModelResponse) -> None:
    span = self._model_span
    if span is None:
        return
    u = response.usage
    span.set_attribute("gen_ai.response.model", response.model)
    span.set_attribute("gen_ai.response.finish_reasons", [str(response.stop_reason)])
    span.set_attribute("gen_ai.usage.input_tokens", u.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", u.output_tokens)
    # None means "not reported" (chapter 3) — omit rather than assert a zero
    if u.cache_read_tokens is not None:
        span.set_attribute("gen_ai.usage.cache_read.input_tokens", u.cache_read_tokens)
    if u.cache_write_tokens is not None:
        span.set_attribute("gen_ai.usage.cache_write.input_tokens", u.cache_write_tokens)
    if u.reasoning_tokens is not None:
        span.set_attribute("gen_ai.usage.reasoning.output_tokens", u.reasoning_tokens)
    if u.cost_usd is not None:
        span.set_attribute("harness.usage.cost_usd", u.cost_usd)   # no gen_ai.* for cost
    if response.raw_stop_reason:
        span.set_attribute("harness.response.raw_stop_reason", response.raw_stop_reason)
    self._end_model_span()
```

**`if ... is not None` is chapter 3's `int | None` decision paying off.** Setting `cache_read.input_tokens = 0` on a vLLM response asserts a measurement that was never taken. Your dashboards then show a confident 0% cache hit rate for a provider that doesn't report caching, and someone goes looking for the bug. Omit the attribute instead — "absent" and "zero" are different facts, and the conventions are built around that distinction.

**`gen_ai.response.finish_reasons` is an array**, even for a single generation. And note we record the *normalized* value while `harness.response.raw_stop_reason` keeps the wire value — when a model does something baffling, that pair tells you what your code decided versus what the server actually said.

### The tool span

```python
def on_tool_start(self, call: ToolCallPart) -> None:
    span = _tracer.start_span(
        f"execute_tool {call.name}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": call.name,
            "gen_ai.tool.call.id": call.id,
            "gen_ai.tool.type": "function",
        },
    )
    self._tool_spans[call.id] = span


def on_tool_end(self, call: ToolCallPart, result: ToolResultPart) -> None:
    span = self._tool_spans.pop(call.id, None)
    if span is None:
        return
    span.set_attribute("harness.tool.is_error", result.is_error)
    span.set_attribute("harness.tool.result_bytes", len(result.content))
    if result.is_error:
        span.set_status(Status(StatusCode.ERROR, result.content[:200]))
    span.end()
```

Keyed by `call.id` because chapter 6 runs tools **in parallel** — a single "current tool span" would be wrong the moment two `read` calls overlap. Chapter 5's ID normalization is what makes the dict key safe.

`set_status(ERROR)` on a failed tool is what makes the error-rate query work in every backend.

### Retries as events

```python
def on_retry(self, attempt: int, error: Exception) -> None:
    if self._model_span is None:
        return
    self._model_span.add_event("retry", {
        "harness.retry.attempt": attempt,
        "harness.retry.error_type": type(error).__name__,
        "harness.retry.message": str(error)[:200],
    })
```

An event, not a child span. A retry is a thing that happened *during* the request, not a separate operation.

This is why chapter 7 put `on_retry` on the observer even though retries happen in chapter 8, below the loop. Without it, a request retried twice looks like one very slow call, and you go looking for a slow model instead of a rate limit.

---

## What must never become an attribute

**API keys.** Obvious, and still worth a rule: never put a whole `Settings`, header dict, or client config on a span. Spans go to a third party and are retained.

**Prompts and responses, by default.** The conventions define `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.tool.call.arguments`, and `gen_ai.tool.call.result` — and mark all of them **Opt-In**, deliberately. For a *coding* agent this matters more than usual: your prompts contain the user's source code, and their tool results contain file contents and command output.

So: off unless asked.

```python
class OTelObserver:
    def __init__(self, *, provider: str, model: str, capture_content: bool = False):
        self._capture = capture_content
```

```python
if self._capture:
    span.set_attribute("gen_ai.tool.call.arguments", json.dumps(call.arguments))
```

Invaluable when debugging a specific bad run. Not a default, and not something to enable for a whole team without saying so.

---

## Checkpoint

### Unit: the spans are right, with no collector

The OTel SDK ships an in-memory exporter for exactly this.

`tests/test_ch10_tracing.py`:

```python
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.tracing import OTelObserver
from harness.loop import run_conversation
from harness.provider import ModelResponse, StopReason, Usage
from harness.tools import Deps, Registry, READ
from harness.transcript import TextPart, ToolCallPart, Transcript


@pytest.fixture(scope="module")
def exporter():
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    trace.set_tracer_provider(provider)      # the APPLICATION (here, the test) does this
    return exp


def test_span_tree_and_attributes(exporter, tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    exporter.clear()

    client = ScriptedClient(                      # from chapter 7
        ModelResponse(content=(ToolCallPart(id="c1", name="read",
                                            arguments={"path": "a.py"}),),
                      stop_reason=StopReason.TOOL_USE,
                      usage=Usage(input_tokens=100, output_tokens=20),
                      model="m", provider="fake", raw_stop_reason="stop"),
        ModelResponse(content=(TextPart("done"),), stop_reason=StopReason.END_TURN,
                      usage=Usage(input_tokens=150, output_tokens=10),
                      model="m", provider="fake"),
    )
    t = Transcript(); t.add_user_text("go")
    run_conversation(t, client, Registry([READ]), Deps(cwd=tmp_path),
                     observer=OTelObserver(provider="fake", model="m"))

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "chat m" in spans
    assert "execute_tool read" in spans
    assert "invoke_agent harness" in spans

    chat = spans["chat m"]
    assert chat.attributes["gen_ai.operation.name"] == "chat"
    assert chat.attributes["gen_ai.provider.name"] == "fake"   # not gen_ai.system
    assert chat.attributes["gen_ai.usage.input_tokens"] in (100, 150)
    assert chat.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)

    tool = spans["execute_tool read"]
    assert tool.attributes["gen_ai.tool.name"] == "read"
    assert tool.attributes["gen_ai.tool.call.id"] == "c1"


def test_unreported_usage_is_omitted_not_zero(exporter, tmp_path):
    exporter.clear()
    client = ScriptedClient(
        ModelResponse(content=(TextPart("hi"),), stop_reason=StopReason.END_TURN,
                      usage=Usage(input_tokens=5, output_tokens=1),   # cache = None
                      model="m", provider="fake"))
    t = Transcript(); t.add_user_text("go")
    run_conversation(t, client, Registry([READ]), Deps(cwd=tmp_path),
                     observer=OTelObserver(provider="fake", model="m"))
    chat = next(s for s in exporter.get_finished_spans() if s.name == "chat m")
    assert "gen_ai.usage.cache_read.input_tokens" not in chat.attributes


def test_content_is_not_captured_by_default(exporter, tmp_path):
    """The default must never put the user's source code into telemetry."""
    (tmp_path / "secret.py").write_text("API_KEY = 'sk-real-key'\n")
    exporter.clear()

    client = ScriptedClient(
        ModelResponse(content=(ToolCallPart(id="c1", name="read",
                                            arguments={"path": "secret.py"}),),
                      stop_reason=StopReason.TOOL_USE, usage=Usage(),
                      model="m", provider="fake"),
        ModelResponse(content=(TextPart("done"),), stop_reason=StopReason.END_TURN,
                      usage=Usage(), model="m", provider="fake"),
    )
    transcript = Transcript()
    transcript.add_user_text("go")
    run_conversation(transcript, client, Registry([READ]), Deps(cwd=tmp_path),
                     observer=OTelObserver(provider="fake", model="m"))

    tool = next(s for s in exporter.get_finished_spans()
                if s.name == "execute_tool read")
    assert "gen_ai.tool.call.arguments" not in tool.attributes
    assert "gen_ai.tool.call.result" not in tool.attributes
    assert "sk-real-key" not in str(tool.attributes)


def test_loop_does_not_import_opentelemetry():
    import ast, inspect, harness.loop
    tree = ast.parse(inspect.getsource(harness.loop))
    mods = {n.module.split(".")[0] for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, ast.Import) for a in n.names}
    assert "opentelemetry" not in mods
```

```bash
pytest tests/test_ch10_tracing.py -v
```

The last test is the architectural one — tracing was added without the loop learning it exists.

### End to end: a real trace

```bash
docker run -d --name otel -p 4318:4318 -p 16686:16686 jaegertracing/all-in-one:latest
```

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
LLM_PROVIDER=anthropic \
harness --cwd /tmp/scratch --trace \
  --once "read buggy.py, fix it, and run it"
```

Open <http://localhost:16686>, pick `harness`. You should see one `invoke_agent harness` root with alternating `chat` and `execute_tool` children, token counts on each `chat`, and any retries as events.

Any OTLP backend works — Grafana Tempo, Honeycomb, an OpenTelemetry Collector, **or Opik**, which is what the reference course uses. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at it and add whatever auth headers it wants. That portability is the whole argument for emitting standard OTLP rather than a vendor SDK.

---

Next: [11-testing.md](11-testing.md) — proving the seam mechanically.

**Sources.** [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai) (`docs/gen-ai/gen-ai-spans.md`, `docs/gen-ai/gen-ai-agent-spans.md`, `docs/registry/attributes/gen-ai.md`) — all attributes Development status at time of writing.
