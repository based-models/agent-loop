# 04 — The Anthropic adapter

The one provider that isn't OpenAI-shaped. Build it first: it's the shape the canonical types were cut from, so the translation is mostly structural, and it forces you to get thinking-block replay right while the codebase is still small.

This chapter builds `src/harness/providers/anthropic.py`.

---

## The lazy import

Chapter 1 made `anthropic` an optional extra. That's only true if nothing imports it at module scope — including this module, because `config.py` imports *this* module to reach the factory, so a top-level `import anthropic` here breaks a Modal-only install at `import harness.config`.

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # type checkers only; no runtime import
    import anthropic


def _sdk() -> Any:
    try:
        import anthropic
    except ImportError as e:
        raise ImportError(
            "The Anthropic provider requires the SDK.\n"
            "  pip install 'harness[anthropic]'"
        ) from e
    return anthropic
```

> **Python note.** `TYPE_CHECKING` is a constant that is `False` when the program runs
> and `True` when a type checker reads it. So the `import anthropic` under it never
> actually executes — mypy sees the types, the running program does not import the SDK.
> The `from __future__ import annotations` line at the top is what lets you *use* those
> names in annotations anyway: it stores annotations as strings instead of evaluating them.

`TYPE_CHECKING` gives you annotations without a runtime cost. The error message names the fix — someone hitting this has misconfigured an install, not written a bug, and a bare `ModuleNotFoundError: anthropic` doesn't tell them which extra they're missing.

---

## The client

```python
class AnthropicClient:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None, model: str = "claude-opus-5",
                 timeout: float = 600.0) -> None:
        sdk = _sdk()
        self.model = model
        # api_key=None lets the SDK do its own credential resolution:
        # ANTHROPIC_API_KEY -> ANTHROPIC_AUTH_TOKEN -> an `ant auth login` profile.
        self._client = sdk.Anthropic(api_key=api_key, timeout=timeout)

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = request_to_wire(request, model=self.model)
        message = self._client.messages.create(**payload)
        return response_from_wire(message)
```

`request_to_wire` and `response_from_wire` are **module-level functions, not methods**.
They take everything they need as arguments and touch no network, which means the whole
translation layer — the part with all the provider quirks in it — is tested by calling a
function with a dict. No client, no API key, no SDK installed.

Three notes.

**`api_key=None` is meaningful**, not lazy. The SDK resolves credentials in order: `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then an OAuth profile written by `ant auth login`. Passing `None` lets all three work; passing `""` breaks the last two. An unset `ANTHROPIC_API_KEY` does not mean there are no credentials.

**`timeout=600.0`** — seconds, and the SDK's own default. Stated explicitly because you're about to set very different values for Modal in chapter 5, and the contrast is the lesson.

**Do not add retries here.** The Anthropic SDK already retries twice by default, with backoff, on 408/409/429/5xx and connection errors. Chapter 8's retry decorator wraps the *other* adapter. Stacking them turns one transient 429 into nine requests and a much longer wall-clock failure.

---

## Canonical → wire

```python
def request_to_wire(request: ModelRequest, *, model: str) -> dict[str, Any]:
    """Canonical request -> the JSON body the Messages API expects."""
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,          # REQUIRED — omitting it is a 400
        "messages": [message_to_wire(m) for m in request.messages],
    }
    if request.system:
        payload["system"] = request.system         # top-level, not a message
    if request.tools:
        payload["tools"] = [tool_to_wire(t) for t in request.tools]
    if request.stop_sequences:
        payload["stop_sequences"] = list(request.stop_sequences)
    if request.temperature is not None:            # D9 — see below
        payload["temperature"] = request.temperature
    if not request.parallel_tool_calls:
        payload["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    return payload
```

### `max_tokens` is required

Not optional, not defaulted server-side. Omit it and you get *"Missing required parameters (`model`, `max_tokens`, `messages`)"*. Chapter 3 made it non-optional in canonical form precisely so this line can't be forgotten.

### `system` is top-level

Never a message. That's the whole reason chapter 2's `Role` has only two values.

### `temperature` — divergence D9, the one that will bite you

```python
if request.temperature is not None:
    payload["temperature"] = request.temperature
```

**`temperature`, `top_p`, and `top_k` were removed from Claude Opus 5, Sonnet 5, Opus 4.8, Opus 4.7, and the Fable line. Sending any of them returns a 400.**

They still work on Opus 4.6, Sonnet 4.6, Haiku 4.5, and older. So the field stays in the canonical request and this adapter forwards it *only when explicitly set*.

If you port the notebook's `chat()` directly:

```python
params = {..., "temperature": temperature, ...}   # temperature=1.0 default
```

every request fails against a current model, and the error won't obviously point at a parameter you never thought about. This is the single most likely thing to break a copy-pasted loop from an older tutorial.

### Tools, and where `strict` goes

```python
def tool_to_wire(tool: ToolSchema) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,     # bare schema — no envelope
    }
    if tool.strict:
        wire["strict"] = True                # TOP-LEVEL on the tool object
    return wire
```

`strict` is a **top-level field on the tool object**, next to `name`. Not inside a nested `function` object (that's OpenAI, chapter 5), and not on `tool_choice`. Getting this wrong is a quiet no-op rather than an error, which is worse.

`strict: true` requires `additionalProperties: false` and every property listed in `required`. Chapter 6 authors schemas that way once, for both providers.

### Messages

```python
def message_to_wire(message: Message) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for part in message.content:
        match part:
            case TextPart():
                blocks.append({"type": "text", "text": part.text})
            case ThinkingPart():
                # Replay verbatim — see below. raw is the SDK block as a dict.
                blocks.append(part.raw if part.raw is not None else {
                    "type": "thinking", "thinking": part.text, "signature": part.signature,
                })
            case ToolCallPart():
                blocks.append({"type": "tool_use", "id": part.id,
                               "name": part.name, "input": part.arguments})
            case ToolResultPart():
                blocks.append({"type": "tool_result", "tool_use_id": part.call_id,
                               "content": part.content, "is_error": part.is_error})
    return {"role": message.role, "content": blocks}
```

> **Python note — `match`.** `match part:` compares `part` against each `case`.
> `case TextPart():` means "if this is a `TextPart`" — the empty parentheses are class
> *pattern* syntax, not a constructor call, and nothing is created. Inside that branch
> the type checker knows `part` is a `TextPart`, so `part.text` type-checks. A chain of
> `elif isinstance(part, TextPart):` would do the same job; `match` is just easier to
> read when there are four branches.

Note what *didn't* happen: no special case for tool results. Chapter 2 put them in a `Message(role="user")` with N `ToolResultPart`s, which is already exactly Anthropic's shape — **all tool results for one assistant turn in a single user message**. Splitting them across messages silently trains the model to stop making parallel calls.

This is the structural payoff of canonicalizing on the richer shape. Chapter 5 has to work for it.

---

## Thinking blocks: not optional

The part it's tempting to skip, and the one that will produce the most confusing bug if you do.

**Thinking is on by default on Claude Opus 5.** Every response you get back may contain `thinking` blocks, and:

- Each carries an opaque **`signature`**.
- Continuing the conversation on the same model requires echoing them back **unchanged**.
- `display` defaults to `"omitted"`, so `thinking.thinking` is usually an **empty string**. The block still matters — the signature is the payload, not the text.
- On newer models a **preserved thinking** check rejects a request whose earlier turns were edited.

That last point is why chapter 2's transcript is append-only. It's an API constraint, not a design preference.

So: capture the whole block, replay it verbatim.

```python
case "thinking":
    parts.append(ThinkingPart(
        text=getattr(block, "thinking", "") or "",
        signature=getattr(block, "signature", None),
        provider="anthropic",
        model=message.model,
        raw=block.model_dump(),     # keep it all; you don't know what matters
    ))
case "redacted_thinking":
    parts.append(ThinkingPart(text="", provider="anthropic",
                             model=message.model, raw=block.model_dump()))
```

Storing `raw=block.model_dump()` rather than reconstructing from named fields is deliberate. You are not the authority on which fields the signature covers; round-trip the whole thing.

If you drop thinking blocks, the symptom is not an immediate error. It's a model that gets subtly worse over a long conversation, or a 400 several turns later on a model that enforces the preserved-thinking check — either way, far from the line that caused it.

To turn thinking off (cheaper, simpler, and fine for a first run), pass `thinking={"type": "disabled"}` — accepted on Opus 5 at effort `high` or below. Prefer low effort over disabled thinking if you're optimizing cost: with thinking disabled, Opus 5 occasionally writes a tool call into *visible text* instead of emitting a `tool_use` block, which in an agent loop means the call silently never runs.

---

## Wire → canonical

```python
def response_from_wire(message: Any) -> ModelResponse:
    """An SDK Message -> the canonical response. Pure: no self, no network."""
    parts: list[ContentPart] = []
    for block in message.content:
        match block.type:
            case "text":
                parts.append(TextPart(block.text))
            case "thinking" | "redacted_thinking":
                ...   # as above
            case "tool_use":
                parts.append(ToolCallPart(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.input),   # already parsed — no json.loads
                ))
            case _:
                continue      # unknown block type: ignore, don't crash

    # ModelResponse requires at least one part (chapter 3), so an empty turn raises
    # ValidationError. It is converted below — the loop only ever sees our errors.

    stop = _STOP.get(message.stop_reason, StopReason.UNKNOWN)
    if any(isinstance(p, ToolCallPart) for p in parts):
        stop = StopReason.TOOL_USE         # Rule 1 — content wins over the wire value

    try:
        return ModelResponse(
            content=tuple(parts),
            stop_reason=stop,
            raw_stop_reason=message.stop_reason,
            usage=usage_from_wire(message.usage),
            model=message.model,
            provider="anthropic",
            request_id=getattr(message, "id", None),
        )
    except ValidationError as e:
        raise ResponseInvalid(
            f"Anthropic returned a response we cannot normalize: {e}",
            provider="anthropic",
        ) from e
```

`block.input` is **already a parsed object**. No `json.loads`. This is the asymmetry chapter 5 has to handle and this one doesn't — and the reason `ToolCallPart.arguments` is typed as a `dict` rather than a string.

### Stop reasons

```python
_STOP = {
    "end_turn":       StopReason.END_TURN,
    "tool_use":       StopReason.TOOL_USE,
    "max_tokens":     StopReason.MAX_TOKENS,
    "stop_sequence":  StopReason.STOP_SEQUENCE,
    "refusal":        StopReason.REFUSAL,
    "pause_turn":     StopReason.UNKNOWN,   # server tools; out of scope
}
```

Two arrive as **HTTP 200** and mean "this did not work":

**`refusal`** — a safety classifier declined. `stop_details` is populated *only* for this stop reason (it's `None` for every other one, so guard before reading), carrying a `category` and `explanation`. Surface it; don't let it read as an answer.

**`model_context_window_exceeded`** — deliberately absent from that table. Chapter 8 raises it as `ContextLengthExceeded` rather than normalizing it, because it's an error that happens to arrive with a 200 status:

```python
if message.stop_reason == "model_context_window_exceeded":
    raise ContextLengthExceeded("input exceeded the model's context window",
                                provider="anthropic", status_code=200)
```

Anything unrecognized → `UNKNOWN`, never `END_TURN` (Rule 2).

### Converting `ValidationError` at the seam

The `try` around the constructor is a general rule, not a special case: **pydantic's
errors are ours, not the caller's.** The loop knows `ProviderError` and its subclasses;
it should never have to catch `ValidationError` from a library it does not import.

The empty-turn case makes it concrete. `ModelResponse.content` carries
`Field(min_length=1)` (chapter 3), so a response with no parts cannot be constructed —
and that is the right home for the rule, because it holds for every adapter rather than
being re-checked in each one. Converting it here means chapter 8 retries it once, which
is what you want: an empty turn is almost always a transient server hiccup rather than a
decision. Letting it through instead fails on the *next* request, far from the cause.

### Usage

```python
def usage_from_wire(raw: Any) -> Usage:
    return Usage(
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", None),
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", None),
    )
```

> **Python note.** `getattr(obj, "name", default)` reads an attribute by name and
> returns `default` if it isn't there, instead of raising `AttributeError`. Used here
> because older SDK versions and different providers don't all report cache fields.

`getattr(..., None)` rather than `or 0` — chapter 3's rule. Anthropic folds reasoning tokens into `output_tokens` and doesn't report them separately, so `reasoning_tokens` stays `None`.

---

## Checkpoint

You can't hit the API yet — `build_client()` is chapter 9. Test the translation directly, which is what you'd want anyway.

`tests/test_ch04_anthropic.py`:

```python
from types import SimpleNamespace

from harness.provider import ModelRequest, StopReason, ToolSchema
from harness.providers.anthropic import (
    message_to_wire, request_to_wire, response_from_wire, tool_to_wire,
)
from harness.transcript import Message, ToolCallPart, ToolResultPart

MODEL = "claude-opus-5"
EMPTY_SCHEMA = {"type": "object", "properties": {},
                "required": [], "additionalProperties": False}


def wire(**kwargs) -> dict:
    """Build a request and translate it, in one step."""
    return request_to_wire(ModelRequest(messages=(), **kwargs), model=MODEL)


def test_temperature_omitted_when_none():
    assert "temperature" not in wire()          # D9: would 400 on Opus 5


def test_temperature_sent_when_explicitly_set():
    assert wire(temperature=0.0)["temperature"] == 0.0   # and 0.0 is not dropped


def test_max_tokens_is_always_present():
    assert "max_tokens" in wire()               # Anthropic requires it


def test_system_is_top_level_not_a_message():
    payload = wire(system="be brief")
    assert payload["system"] == "be brief"
    assert all(m["role"] != "system" for m in payload["messages"])


def test_strict_is_top_level_on_the_tool():
    tool = tool_to_wire(ToolSchema(name="read", description="d",
                                   parameters=EMPTY_SCHEMA))
    assert tool["strict"] is True               # cf. OpenAI, where it is nested
    assert tool["input_schema"] == EMPTY_SCHEMA


def test_tool_results_stay_in_one_user_message():
    payload = message_to_wire(Message(role="user", content=(
        ToolResultPart(call_id="c1", name="read", content="a"),
        ToolResultPart(call_id="c2", name="read", content="b"),
    )))
    assert payload["role"] == "user"
    assert len(payload["content"]) == 2         # one message, two blocks


def test_tool_calls_force_the_tool_use_stop_reason():
    message = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="c1", name="read",
                                 input={"path": "a.py"})],
        stop_reason="end_turn",                 # the wire says end_turn...
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model=MODEL, id="msg_1",
    )
    response = response_from_wire(message)
    assert response.stop_reason is StopReason.TOOL_USE   # ...Rule 1 overrides it
    assert response.raw_stop_reason == "end_turn"        # wire value preserved


def test_unknown_stop_reason_is_not_end_turn():
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")],
        stop_reason="some_future_reason",
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model=MODEL, id="msg_2",
    )
    assert response_from_wire(message).stop_reason is StopReason.UNKNOWN
```

> **Python note.** `SimpleNamespace(a=1)` makes a throwaway object whose attributes you
> set in the constructor — `obj.a` is `1`. It is the quickest way to stand in for an SDK
> response object when all your code does is read attributes off it.

```bash
pytest tests/test_ch04_anthropic.py -v
```

Eight pass, with no network and no SDK installed.

That second property is the point. These tests call plain functions, so there is no client to construct, no API key to fake, and nothing to monkeypatch. If a test here needed `anthropic` on disk, the lazy import would be wrong — which makes this suite a check on chapter 1's optional extras as well as on the translation.

---

Next: [05-openai-compatible.md](05-openai-compatible.md) — one adapter, two providers.

**Sources.** Anthropic API behavior in this chapter (removed sampling parameters, required `max_tokens`, top-level `strict`, thinking replay and preserved thinking, `stop_details` only on `refusal`, SDK credential resolution and default retries) is from the bundled `claude-api` skill reference, current as of this writing.
