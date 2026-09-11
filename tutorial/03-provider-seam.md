# 03 — The provider seam

One file, one protocol, one method. Everything vendor-specific lives below this line; the loop lives above it and never learns a vendor name.

This chapter builds `src/harness/provider.py` and extends `src/harness/errors.py`.
Both import pydantic and nothing else — no provider SDK, which is the line chapter 11's
seam test actually enforces.

---

## The protocol

```python
class LLMClient(Protocol):
    name: str      # "anthropic" | "openrouter" | "modal" — for logs and traces
    model: str     # the resolved model id

    def complete(self, request: ModelRequest) -> ModelResponse: ...
```

That's the whole seam.

> **Python note — `Protocol`.** A `Protocol` describes what an object must *have*,
> not what it must *inherit from*. Any class with a `name` attribute, a `model`
> attribute, and a `complete` method that takes a `ModelRequest` and returns a
> `ModelResponse` counts as an `LLMClient` — it never imports or subclasses anything of
> yours. That is called **structural typing**, and it is why the fake client in chapter
> 11 is ten lines with no base class. Type checkers verify it; at runtime it is just a
> normal object.

**`Protocol`, not ABC.** Structural typing means adapters don't import your core to subclass it, there's no registration step, and the fake in chapter 11 is ten lines with no base class. Don't add `@runtime_checkable` — you never need `isinstance` here, and adding it invites someone to.

**One method, and it's pure.** "Pure" here means: it reads its argument, makes the
request, returns a value — and changes nothing else. It doesn't append to the transcript,
doesn't set an attribute on the client, doesn't write a file. So calling it twice with
the same request is exactly as safe as calling it once, which is what makes the blind
retries in chapter 8 correct. That is a second, independent reason for this shape, on top
of testability.

A sibling is reserved but not implemented:

```python
    # def complete_stream(self, request: ModelRequest) -> Iterator[StreamEvent]: ...
```

Note the return type: `Iterator`, not `AsyncIterator`. **Streaming does not require async** (D11). Chapter 12.

---

## The request

```python
class ToolSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, Any]          # bare JSON Schema, object type
    strict: bool = True


ToolChoice: TypeAlias = Literal["auto", "none", "required"]


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    max_tokens: int = Field(default=16_000, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stop_sequences: tuple[str, ...] = ()
    tool_choice: ToolChoice = "auto"
    parallel_tool_calls: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)
```

> **Python note.** `Field(default=16_000, gt=0)` sets a default *and* a constraint:
> greater than zero. `ge` / `le` are "greater or equal" / "less or equal". These run at
> construction, so a bad value fails where it was written rather than as a 400 from a
> provider ten seconds later.

### `system` is a field, not a message

Chapter 2 already removed `"system"` from `Role`; this is where it lands instead.

Anthropic takes `system` as a top-level parameter. OpenAI-compatible servers take it as a message. Making it a *request field* means the adapter decides how to render it — and, more usefully, means **you cannot emit two system messages**, which is a real 400 on vLLM chat templates that `raise_exception` on a second or non-leading system message.

The tradeoff, stated honestly: mid-conversation operator instructions have no portable form. Anthropic has a first-class feature for them; on OpenAI-compatible servers you'd append a user-role text block. Out of scope here, but that's the seam if you want it.

> **A pydantic gotcha worth knowing now.** Pydantic reserves the `model_` prefix for its
> own methods, so a field literally named `model` triggers a warning. `ModelResponse`
> below has one — it's the provider's model id, and the name is right — so it declares
> `model_config = ConfigDict(protected_namespaces=())` to turn the warning off. You'll
> hit this the moment you name a field `model_name` or similar.

### `max_tokens: int`, not `int | None`

**Anthropic requires it.** Omitting it is a 400: *"Missing required parameters (`model`, `max_tokens`, `messages`)"*. OpenAI-compatible servers treat it as optional.

Canonical takes the stricter rule and always sends it. A default of 16,000 keeps non-streaming responses under SDK HTTP timeouts; go higher and you should be streaming.

There's a Modal wrinkle: vLLM 400s when `prompt_tokens + max_tokens > max_model_len`, with the memorable message `max_tokens must be at least 1, got -186`. Chapter 8 classifies that as `ContextLengthExceeded` rather than letting it read as a validation error.

### `temperature: float | None = None` — divergence D9

The notebook hardcodes `temperature=1.0`. **That returns a 400 on Claude Opus 5, Sonnet 5, Opus 4.8, Opus 4.7, and the Fable line** — `temperature`, `top_p`, and `top_k` were removed from those models.

They're still accepted on Opus 4.6, Sonnet 4.6, Haiku 4.5, older Claude models, and every OpenAI-compatible provider. So the parameter stays — it just has to be *genuinely* optional, where `None` means **omit the key entirely**, not *send a default*.

```python
if request.temperature is not None:
    payload["temperature"] = request.temperature
```

Never `payload["temperature"] = request.temperature or 1.0`. That reintroduces the bug and also silently rewrites a deliberate `0.0`.

### `ToolSchema.parameters` is the *bare* schema

Anthropic wants `{"name", "description", "input_schema"}`. OpenAI wants `{"type": "function", "function": {"name", "description", "parameters"}}`.

The OpenAI envelope carries zero information — it's packaging. Store the bare schema (Anthropic's shape) and let each adapter wrap it.

`strict: true` is worth knowing precisely, because the two are *almost* the same and the difference will bite:

- **Anthropic**: `strict` is a **top-level field on the tool object**, alongside `name` / `description` / `input_schema`. Not on `tool_choice`.
- **OpenAI-compatible**: `strict` goes **inside** the `function` object.

Both require `additionalProperties: false` and every property listed in `required`. Chapter 6 authors schemas in that dialect once so the same dict is valid for both.

And never *rely* on `strict` for correctness. vLLM's own docs are blunt about the non-strict path: it extracts tool calls from raw text, so arguments may be malformed or violate the schema. You validate yourself (chapter 6).

---

## The response

```python
class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    # None means "not reported" — which is NOT the same fact as 0.
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class ModelResponse(BaseModel):
    # `model` is a real field name here; silence pydantic's model_ namespace warning.
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    content: tuple[ContentPart, ...] = Field(min_length=1)
    stop_reason: StopReason
    usage: Usage
    model: str
    provider: str
    raw_stop_reason: str | None = None   # verbatim wire value, for logs
    request_id: str | None = None
```

`Field(min_length=1)` on `content` is failure mode **F8** from chapter 7, moved into the
type: an adapter *cannot* hand back an empty assistant turn, because the object won't
construct. Chapters 4 and 5 catch the resulting `ValidationError` and re-raise it as
`ResponseInvalid`, which chapter 8 retries once.

### Why the cache fields are `int | None`

vLLM omits `prompt_tokens_details` entirely. If you default those to `0`, your cache diagnostics will confidently report a 0% hit rate on a provider that simply doesn't measure it — and you'll go looking for a caching bug that doesn't exist.

`None` = not reported. `0` = reported as zero. Different facts; don't conflate them.

Field mapping across the three:

| Canonical | Anthropic | OpenAI / OpenRouter | vLLM |
|---|---|---|---|
| `input_tokens` | `usage.input_tokens` | `usage.prompt_tokens` | `usage.prompt_tokens` |
| `output_tokens` | `usage.output_tokens` | `usage.completion_tokens` | `usage.completion_tokens` |
| `cache_read_tokens` | `usage.cache_read_input_tokens` | `usage.prompt_tokens_details.cached_tokens` | **absent → None** |
| `cache_write_tokens` | `usage.cache_creation_input_tokens` | `usage.prompt_tokens_details.cache_write_tokens` | **absent → None** |
| `reasoning_tokens` | folded into `output_tokens` | `usage.completion_tokens_details.reasoning_tokens` | **absent → None** |
| `cost_usd` | — | OpenRouter `usage.cost` | — |

Don't store `total_tokens`. It's derivable, and the two sides disagree about what it includes.

### `raw_stop_reason`

Keep the verbatim wire value. When a model does something baffling, the normalized enum tells you what your code decided and `raw_stop_reason` tells you what the server actually said. That gap is where the bug is.

---

## What `complete()` is allowed to raise

The seam is not just the return type. A caller has to know what can come *out* of it, so
the exception hierarchy is part of the contract and belongs here — chapters 4 and 5 both
raise from it, and chapter 8 is where the mapping from HTTP responses gets built.

Extend `src/harness/errors.py`, which chapter 2 started with `HarnessError` and
`TranscriptError`.

These stay **plain exception classes, not pydantic models** — deliberately. An exception
is raised and caught, never parsed or serialized, so validation buys nothing; and
`BaseModel` doesn't inherit from `Exception`, so making them models would mean carrying a
model *inside* an exception for no gain. Pydantic is for data that crosses a boundary.
Exceptions don't.

The module also imports nothing at all, which is what forces provider-specific
classification to live in the adapters where the vendor knowledge already is.

```python
class ProviderError(HarnessError):
    retryable: bool = False

    def __init__(self, message: str, *, provider: str | None = None,
                 status_code: int | None = None, retry_after: float | None = None,
                 request_id: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        self.request_id = request_id


class AuthError(ProviderError): ...               # 401 / 403, missing Modal headers
class BadRequestError(ProviderError): ...         # 400 — usually YOUR bug
class ContextLengthExceeded(BadRequestError): ...
class InsufficientCredits(ProviderError): ...     # OpenRouter 402
class ModelNotFound(ProviderError): ...           # 404

class RateLimited(ProviderError):         retryable = True
class ProviderUnavailable(ProviderError): retryable = True   # 5xx / 502 / 503 / 529
class ProviderTimeout(ProviderError):     retryable = True
class ResponseInvalid(ProviderError):     retryable = True   # a 200 we can't normalize
```

> **Python note.** A class body of just `...` is a complete class — it inherits
> everything and adds nothing. That is the whole point here: these subclasses exist so
> that `except AuthError:` can be told apart from `except RateLimited:`. The name *is*
> the information.

**`retryable` is a class attribute, not a constructor argument.** Whether a rate limit is
worth retrying is a fact about the *kind* of error, not about the call site. Putting it on
the class means chapter 8's retry logic is one `if` and there is exactly one place to
change the answer.

The three-way split is the part to carry forward:

- **Your bug** — `BadRequestError`, `AuthError`, `ModelNotFound`. Retrying is pure waste.
- **Their problem, temporarily** — `RateLimited`, `ProviderUnavailable`, `ProviderTimeout`. Retry.
- **Nobody's fault, structurally** — `ContextLengthExceeded`, `InsufficientCredits`. Retrying makes it worse.

Two more go in the same file, raised by code above the seam rather than by an adapter.
Add them now so the module is complete:

```python
class ToolError(HarnessError): ...            # chapter 6 — a tool failed usefully
class LoopBudgetExceeded(HarnessError): ...   # chapter 7 — out of iterations or time
```

## `StopReason`, and the two rules that make it trustworthy

> **Python note — `StrEnum`.** An `Enum` is a fixed set of named values, so you write
> `StopReason.TOOL_USE` instead of the bare string `"tool_use"` and a typo becomes an
> `AttributeError` instead of a silent mismatch. `StrEnum` (Python 3.11+) additionally
> makes each member a real `str`, so it prints readably and serializes to JSON without
> conversion. Compare members with `is` — `stop_reason is StopReason.TOOL_USE`.

```python
class StopReason(StrEnum):
    END_TURN      = "end_turn"       # anthropic end_turn      | openai stop
    TOOL_USE      = "tool_use"       # anthropic tool_use      | openai tool_calls
    MAX_TOKENS    = "max_tokens"     # anthropic max_tokens    | openai length
    STOP_SEQUENCE = "stop_sequence"  # anthropic stop_sequence | openai stop
    REFUSAL       = "refusal"        # anthropic refusal       | openai content_filter
    ERROR         = "error"          # OpenRouter: failure inside a 200
    UNKNOWN       = "unknown"        # never silently collapse into END_TURN
```

The notebook only ever checks `stop_reason != "tool_use"`. Here is what it's collapsing:

| Canonical | Anthropic | OpenAI-compatible |
|---|---|---|
| `TOOL_USE` | `tool_use` | `tool_calls` |
| `END_TURN` | `end_turn` | `stop` |
| `MAX_TOKENS` | `max_tokens` | `length` |
| `STOP_SEQUENCE` | `stop_sequence` (+ a `stop_sequence` field) | `stop` — indistinguishable |
| `REFUSAL` | `refusal` (+ `stop_details.category`, HTTP **200**) | `content_filter` |
| `ERROR` | — | OpenRouter `error`, inside a **200** |
| → `ContextLengthExceeded` | `model_context_window_exceeded` (HTTP **200**) | a 400 |
| `UNKNOWN` | anything new | `null`, or anything new |

Three of those arrive as HTTP 200 while meaning "this did not work." A loop that only branches on `!= "tool_use"` treats a refusal and a context overflow as a completed answer.

### Rule 1 — tool calls win

> **If the normalized content contains any `ToolCallPart`, `stop_reason` is `TOOL_USE`, regardless of what the wire said.**

Enforced in every adapter. This single rule papers over the most common self-hosted failure: a vLLM tool-call parser populates `tool_calls` while `finish_reason` comes back as `"stop"` or `null`. Trust the content — it's the thing the model actually produced. Keep the wire value in `raw_stop_reason`.

### Rule 2 — `UNKNOWN` is not `END_TURN`

> **Unrecognized finish reasons map to `UNKNOWN`, never to `END_TURN`.**

Folding them in makes a server returning `null` look exactly like the model finishing its answer. The loop then stops, the user sees an empty response, and nothing anywhere logs a problem. Chapter 7's loop treats `UNKNOWN`-with-no-tool-calls as terminal *but logs a warning*, which is the difference between a silent failure and a five-minute diagnosis.

---

## Why the loop must never see a vendor type

Not a principle — five concrete failures:

1. **Contradictory truth.** `stop_reason == "tool_use"`, `finish_reason == "tool_calls"`, and `"stop"`-with-populated-`tool_calls` all mean the same thing. If the loop reads wire values it must know all three dialects, and then there is no abstraction — only indirection.
2. **Test cost.** Building an `anthropic.types.Message` needs eight fields you don't care
   about. Building a `ModelResponse` needs the four you do — and pydantic tells you
   immediately if you got one wrong.
3. **Install cost.** A Modal-only consumer shouldn't have `anthropic` on disk. Chapter 1 made it an extra; this is what keeps that true.
4. **Drift containment.** A new content block type breaks exactly one `match`, in one adapter.
5. **Persistence.** `model_dump_json()` on your own models is a stable on-disk format
   that you control. SDK types are stable until the next minor release.

### Where the line actually is

Core imports **pydantic**, and that is fine. The rule is not "core has no dependencies" —
it is **core knows about no specific LLM provider**. Pydantic is a data-modelling
library; `anthropic` and `httpx` are how you talk to one vendor. Those are different
kinds of dependency and the test draws the line between them:

Chapter 11 makes this mechanical with a dozen lines of `ast`. Worth writing now if you're impatient:

```python
def test_core_has_no_vendor_imports():
    banned = {"anthropic", "openai", "httpx", "requests"}    # note: NOT pydantic
    for path in (SRC / "harness").glob("*.py"):     # note: NOT providers/
        tree = ast.parse(path.read_text())
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        assert not (roots & banned), f"{path.name} imports {roots & banned}"
```

---

## Checkpoint

`tests/test_ch03_provider.py`:

```python
import pytest
from pydantic import ValidationError

from harness.provider import (
    LLMClient, ModelRequest, ModelResponse, StopReason, Usage,
)
from harness.transcript import Message, TextPart


class EchoClient:
    """A plain class. It imports nothing from us and subclasses nothing."""

    name = "fake"
    model = "fake-1"

    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=(TextPart(text="ok"),),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=1, output_tokens=1),
            model=self.model,
            provider=self.name,
        )


def test_a_plain_class_satisfies_the_protocol():
    client: LLMClient = EchoClient()          # structural typing; no base class
    request = ModelRequest(messages=(Message(role="user",
                                             content=(TextPart(text="hi"),)),))
    assert client.complete(request).stop_reason is StopReason.END_TURN


def test_unreported_cache_tokens_are_none_not_zero():
    assert Usage().cache_read_tokens is None
    assert Usage().input_tokens == 0


def test_temperature_defaults_to_omitted():
    assert ModelRequest(messages=()).temperature is None


def test_max_tokens_must_be_positive():
    with pytest.raises(ValidationError):
        ModelRequest(messages=(), max_tokens=0)


def test_an_empty_response_cannot_be_constructed():
    # F8, enforced by the type: an adapter physically cannot return an empty turn
    with pytest.raises(ValidationError):
        ModelResponse(content=(), stop_reason=StopReason.END_TURN,
                      usage=Usage(), model="m", provider="p")


def test_negative_usage_is_rejected():
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


```bash
pytest tests/test_ch03_provider.py -v
mypy
```

Six pass, and mypy is clean.

The first test is the architectural one: a class that imports nothing from you satisfies
your seam. That's what chapter 11's `FakeClient` relies on, and it stays true with
pydantic in the picture — `LLMClient` is still a `Protocol`, not a base class, so nothing
forces an implementation to be a model.

The last three are new. Each was previously a bug you could only find at runtime, and is
now a construction error at the line that caused it.

---

Next: [04-anthropic-adapter.md](04-anthropic-adapter.md) — the provider that isn't OpenAI-shaped.

**Sources.** Anthropic claims here (required `max_tokens`, removed sampling parameters, top-level `strict`, the `refusal` and `model_context_window_exceeded` stop reasons) come from the bundled `claude-api` skill reference. · [OpenAI chat completion object](https://developers.openai.com/api/docs/api-reference/chat/object) (`finish_reason`, `usage`) · [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) (the non-strict malformed-arguments caveat).
