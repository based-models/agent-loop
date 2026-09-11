# 02 — The transcript

The conversation is the agent's entire memory. Every bug you will spend an afternoon on lives in here.

This chapter builds `src/harness/transcript.py`. **Zero third-party imports** — enforced by test in chapter 11.

---

## Why not just `list[dict]`?

The notebook uses `messages: list[dict]` and it works fine, for one provider, in a notebook. Three things break it at scale:

1. **It can represent illegal states.** A `tool_use` with no matching `tool_result` is a
   valid `list[dict]` and an instant 400 from Anthropic. You want the type system and one
   constructor to make that unreachable.
2. **`content` is sometimes a string and sometimes a list.** Every function that touches a
   message grows an `isinstance` branch.
3. **It has no round trip.** Persisting it, diffing it in a test, or replaying it means
   hand-writing serialization for each provider's dialect.
4. **Nothing checks it.** A typo in a key, a missing field, an integer where a string
   belongs — all of it sails through and fails somewhere else entirely.

These are pydantic's four problems. Models give you constrained types for the first,
one shape for the second, `model_dump_json` / `model_validate_json` for the third, and
validation at construction for the fourth.

---

## The parts

```python
from __future__ import annotations

from typing import Annotated, Any, Literal, Sequence, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

Role: TypeAlias = Literal["user", "assistant"]


class Part(BaseModel):
    """Base for every content part: immutable, and rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TextPart(Part):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(Part):
    """Reasoning emitted by the model. `text` may be "" when the provider omits it."""

    type: Literal["thinking"] = "thinking"
    text: str
    signature: str | None = None   # Anthropic: opaque, must be echoed back verbatim
    provider: str | None = None    # who minted it — gates replay
    model: str | None = None       # which model minted it — gates replay
    raw: Any = None                # provider-native payload, for verbatim replay


class ToolCallPart(Part):
    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)      # provider-minted, opaque, unique within the turn
    name: str = Field(min_length=1)
    arguments: dict[str, Any]          # ALWAYS parsed. Never a JSON string.
    raw_arguments: str | None = None   # the verbatim wire string, for replay
    parse_error: str | None = None     # non-None => arguments is {} and unusable


class ToolResultPart(Part):
    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)  # == the ToolCallPart.id this answers
    name: str
    content: str                        # already-serialized result text
    is_error: bool = False


# A DISCRIMINATED union: pydantic reads the "type" field to decide which class to build.
ContentPart: TypeAlias = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: tuple[ContentPart, ...] = Field(min_length=1)
```

> **Python note — pydantic.** A `BaseModel` is a class whose fields are *checked* when
> you build one. `TextPart(text=123)` raises `ValidationError` instead of quietly
> storing an integer that breaks something three functions later. `ConfigDict` is where
> per-model settings go: `frozen=True` makes instances read-only, and `extra="forbid"`
> rejects fields you didn't declare — so a typo like `TextPart(txt="hi")` is an error,
> not a silently-ignored keyword.
>
> `Field(min_length=1)` is a constraint on the value. `Field(discriminator="type")` is
> explained below; it's the reason every part carries that redundant `type`.

Six decisions in there. Each one pays for itself somewhere specific.

### 1. `content` is always a tuple of parts

Never a bare `str`. Normalize on the way in, once, in the adapter.

This is the highest-value line in the file, and the payoff is negative code: it deletes
the `isinstance(content, str)` branch from every function you will write for the rest of
the project. `Message(role="user", content=(TextPart(text="hi"),))` is more typing than
`{"role": "user", "content": "hi"}` exactly once, at the constructor, and cheaper
everywhere after.

A tuple rather than a list because `frozen=True` stops you *reassigning* the field but
would happily let you `.append()` to a list inside it. A tuple can't be appended to, so
the message is genuinely read-only.

`Field(min_length=1)` on it means an empty message cannot be constructed at all — the
first of the five invariants below, enforced by the type rather than by a check you have
to remember to call.

### 2. There is no `"system"` role and no `"tool"` role

Look at `Role` again: `Literal["user", "assistant"]`. That's all.

**System lives on the request, not in the messages** (chapter 3). This is not tidiness —
it is a bug class deleted by construction. Many vLLM chat templates call
`raise_exception` when a system message appears second, or appears anywhere but first. If
`system` is a field on `ModelRequest`, emitting two of them is not a thing you can do.

**Tool results are `ToolResultPart`s inside a `Message(role="user")`** — the Anthropic
grouping, not OpenAI's N separate `role: "tool"` messages. Because the grouping encodes
the invariant: *these N results answer the N calls from that one assistant turn.* That's
exactly what you need to enforce, and it's much easier to enforce on a structure that
states it. Fanning out to N messages for OpenAI is a loop; coalescing N messages back
requires lookahead.

### 3. Tool call IDs are opaque, provider-minted, and never touched

`toolu_01A09q90qw90lq917835lq9` (Anthropic), `call_abc123` (OpenAI/OpenRouter),
`chatcmpl-tool-...` (vLLM).

The loop never parses one, never constructs one, never rewrites one. The **adapter**
guarantees they are non-empty and unique within a response — minting `f"call_{i}"` where
a server omits them — so by the time the loop sees an ID, it's an ID the adapter can send
back. `Field(min_length=1)` makes "forgot to mint one" a loud failure at the adapter
rather than a quiet one at the API.

### 4. `arguments` is always a parsed `dict`

Anthropic gives you `tool_use.input`, already an object. OpenAI-compatible servers give
you `function.arguments`, a **string** that may be malformed, may be `""`, and (on some
gateways) may arrive already parsed as a dict.

Parse **once, in the adapter**. If the canonical type were a string, every consumer
downstream would have to handle JSON decode failure — which is precisely the thing an
abstraction exists to stop happening more than once.

`raw_arguments` keeps the verbatim string for replay, and `parse_error` records a failure
without losing the part. Chapter 5 has the parsing; chapter 6 has what the executor does
with `parse_error`.

### 5. The `type` field is a discriminator, not decoration

In a plain dataclass version, that redundant `type: Literal[...]` would be something you
carry so your own loader can tell the classes apart. With pydantic it does real work:

```python
ContentPart = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]
```

`Field(discriminator="type")` tells pydantic: when you see `{"type": "tool_call", ...}`,
build a `ToolCallPart`. Not "try each of the four and keep whichever succeeds" — read one
field, go straight to the right class, and report errors against *that* class.

That is what makes `Transcript.model_validate_json(...)` below a single call with no
loader of your own, and it is why unknown part types fail with
`Input tag 'video' found using 'type' does not match any of the expected tags` instead of
four stacked union errors.

### 6. `frozen=True`, and `extra="forbid"`

`frozen` gives you structural `__eq__` for free, which is what makes
`assert transcript == expected` a viable test strategy over whole conversations. It also
makes accidental mutation a `ValidationError` at the line that did it — which matters
because of the append-only rule below.

`extra="forbid"` is the one that catches real bugs. Without it, a provider response
carrying a field you didn't model is silently dropped; with it, you find out. It also
turns a keyword typo into an error at construction.

> **On performance.** Pydantic validates on every construction, and a long agent run
> builds thousands of parts. Pydantic v2's validator is implemented in Rust and costs
> single-digit microseconds per small model — against an LLM call measured in seconds.
> This is not a hot loop. If it ever became one, `Model.model_construct(...)` skips
> validation, but reach for it only with a measurement in hand.

### On `ThinkingPart` — not decorative

It's tempting to drop reasoning blocks on the floor. You can't, on Anthropic.

Thinking is **on by default** on Claude Opus 5, and thinking blocks carry an opaque
`signature`. Continuing a conversation on the same model requires echoing them back
**unchanged**. On newer models there is an additional *preserved thinking* check that
rejects a request whose earlier turns were edited.

That constraint is why the append-only rule below is a hard requirement rather than a
style preference — and why `ThinkingPart` carries `raw`, `provider`, and `model`. Replay
policy is a per-adapter capability, never loop logic:

| Provider | Replay |
|---|---|
| Anthropic | Verbatim, including `signature` |
| OpenRouter | `reasoning_details` verbatim, **only if** provider and model are unchanged |
| Modal / vLLM | Drop — `reasoning_content` is generally not replayable and most templates discard it |

`raw: Any` is deliberately untyped. You are not the authority on which fields a
provider's signature covers, so you round-trip the whole payload rather than modelling
it.

## The `Transcript`

Divergence **D10**: you own this, rather than a framework owning it.

A bare `list[Message]` would work. Don't use one — route every mutation through a model,
because the invariant you care about most is a *data structure* invariant and this is the
cheapest place to enforce it.

```python
class Transcript(BaseModel):
    """The running conversation. Append-only; see below for why that is not optional."""

    messages: list[Message] = Field(default_factory=list)

    # --- append-only mutation (replaces the notebook's add_*_message) ---

    def add_user_text(self, text: str) -> None: ...
    def add_assistant(self, parts: Sequence[ContentPart]) -> None: ...
    def add_tool_results(self, results: Sequence[ToolResultPart]) -> None: ...

    # --- queries ---

    def pending_tool_calls(self) -> tuple[ToolCallPart, ...]:
        """Tool calls in the last assistant message. () if none."""

    def check_invariants(self) -> None:
        """Raise TranscriptError if the transcript is not sendable."""
```

Note what is **not** there: no `pop`, no `__setitem__`, no `truncate`, no `replace`.
**Append-only**, for the preserved-thinking reason above. When you get to compaction
(chapter 12), the move is to build a *new* transcript, not to edit this one.

`Transcript` is a `BaseModel` but deliberately **not** frozen — it is the one mutable
thing in the file, and the three `add_*` methods are the only way to change it.

### Why `check_invariants` is a method, not a `@model_validator`

The obvious pydantic move is to validate the whole transcript automatically on every
change. Don't: **a correct transcript is invalid halfway through a turn.**

The sequence is: append an assistant message containing two tool calls → run the tools →
append the results. Between those steps the transcript has unanswered tool calls, which
is exactly what `check_invariants` rejects. A model validator firing on mutation would
make the normal path impossible.

So the rule is: **pydantic validates what a single object must always be true of**
(a message has at least one part, a tool call id is non-empty); **`check_invariants`
validates what must be true across messages, at the moments you're about to send.**
Chapter 7's loop calls it before `complete()` in debug and test runs.

### The one method that earns its keep

```python
def add_tool_results(self, results: Sequence[ToolResultPart]) -> None:
    pending = self.pending_tool_calls()
    want = {call.id for call in pending}
    got = {result.call_id for result in results}
    if want != got:
        raise TranscriptError(
            f"tool result mismatch: calls={sorted(want)} results={sorted(got)} "
            f"(missing={sorted(want - got)} unexpected={sorted(got - want)})"
        )
    order = {call.id: index for index, call in enumerate(pending)}
    ordered = tuple(sorted(results, key=lambda result: order[result.call_id]))
    self.messages.append(Message(role="user", content=ordered))
```

> **Python note.** `{call.id for call in pending}` is a **set comprehension** — walk the
> items, collect one value from each into a set. Comparing two sets with `==` ignores
> order, which is what you want here: the calls and results must be the same *group*, and
> the next two lines handle putting them in the right order.
>
> `order` maps each call id to its position, so
> `sorted(results, key=lambda r: order[r.call_id])` sorts the results into the order the
> calls were made in. `key=` takes a function that turns each item into the value to sort
> by; `lambda r: ...` is a small unnamed function of one argument.

Set equality, then re-order to match the calls.

Without this, a dropped result produces:

```
400 `tool_use` ids were found without `tool_result` blocks immediately after:
toolu_01Abc... Each `tool_use` block must have a corresponding `tool_result`
block in the next message.
```

— from the *next* request, with no indication of which earlier line dropped it. With it,
you get a `TranscriptError` naming the exact missing ID, raised at the line that caused
it, with no network round trip.

The re-ordering is not cosmetic. Chapter 6 runs tools in parallel, so results come back
in completion order; OpenAI-compatible servers expect `role: "tool"` messages in
`tool_calls` order.

This is worth contrasting with the reference implementation. `decode` carries a
`_heal_dangling_tool_calls()` method that scans for unpaired calls and synthesizes fake
"tool call interrupted" returns to un-brick a session. That's a reasonable repair for a
state it can reach. `add_tool_results` makes the state unreachable, so there is nothing
to repair.

### `check_invariants`

The cross-message rules. Call it before every `complete()` in tests and debug runs:

```python
def check_invariants(self) -> None:
    for index, message in enumerate(self.messages):
        if message.role != "assistant":
            continue
        calls = [p for p in message.content if isinstance(p, ToolCallPart)]
        if not calls:
            continue

        ids = [call.id for call in calls]
        if len(set(ids)) != len(ids):
            raise TranscriptError(f"message {index} has duplicate tool_call ids: {ids}")

        if index + 1 >= len(self.messages):
            raise TranscriptError(
                f"message {index} ends with unanswered tool calls {ids}; "
                "the transcript is not sendable"
            )
        answered = {p.call_id for p in self.messages[index + 1].content
                    if isinstance(p, ToolResultPart)}
        if answered != set(ids):
            raise TranscriptError(
                f"message {index} calls {sorted(set(ids))} "
                f"but message {index + 1} answers {sorted(answered)}"
            )
```

Five conditions, and note that **two of them are already gone** — pydantic enforces them
on the objects themselves:

| Condition | Enforced by | Otherwise |
|---|---|---|
| No empty messages | `Field(min_length=1)` on `content` | Anthropic rejects empty text blocks; vLLM templates break |
| No empty tool call IDs | `Field(min_length=1)` on `id` | Results can't be matched back |
| Tool call IDs unique within a turn | `check_invariants` | Results can't be matched back |
| Assistant turn with calls is followed by a user turn | `check_invariants` | The classic unpaired-`tool_use` 400 |
| That turn's results match the calls exactly | `check_invariants` | Same |

That split is the general pattern to carry through the rest of the tutorial: **if a rule
is about one object, it belongs on the model; if it is about the relationship between
objects, it belongs in an explicit check.**

---

## Serialization, for free

This is where the refactor pays for itself. The whole session log is:

```python
path.write_text(transcript.model_dump_json(indent=2))
```

and loading it back — with full validation, and the discriminated union picking the right
part class for every entry — is:

```python
transcript = Transcript.model_validate_json(path.read_text())
```

No `asdict`, no hand-written loader, no `match` on `part["type"]`. A corrupted or
hand-edited log fails with a message naming the exact field, instead of producing a
`Transcript` full of dicts that breaks somewhere later.

For an append-only JSONL log — one message per line, so a crashed run still leaves a
readable file — it's one line per message:

```python
def append_jsonl(path: Path, messages: Sequence[Message]) -> None:
    with path.open("a") as f:
        for message in messages:
            f.write(message.model_dump_json() + "\n")


def load_jsonl(path: Path) -> Transcript:
    messages = [Message.model_validate_json(line)
                for line in path.read_text().splitlines() if line.strip()]
    return Transcript(messages=messages)
```

Chapter 9's `--dump-transcript` uses this, and chapter 11's golden tests diff
`model_dump()` output.

One caveat: `ThinkingPart.raw` holds a provider-native payload typed as `Any`, so
pydantic passes it through without validating it. It is JSON-serializable in practice for
all three providers; if you ever store something that isn't, this is where it surfaces.

---

## Checkpoint

`tests/test_ch02_transcript.py`:

```python
import pytest
from pydantic import ValidationError

from harness.errors import TranscriptError
from harness.transcript import (
    Message, TextPart, ToolCallPart, ToolResultPart, Transcript,
)


def test_pairing_enforced_on_add():
    transcript = Transcript()
    transcript.add_user_text("read a.py and b.py")
    transcript.add_assistant([
        TextPart(text="On it."),
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
        ToolCallPart(id="c2", name="read", arguments={"path": "b.py"}),
    ])
    with pytest.raises(TranscriptError, match=r"missing=\['c2'\]"):
        transcript.add_tool_results([
            ToolResultPart(call_id="c1", name="read", content="..."),
        ])


def test_results_reordered_to_call_order():
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([
        ToolCallPart(id="c1", name="read", arguments={}),
        ToolCallPart(id="c2", name="read", arguments={}),
    ])
    # results arrive out of order, as they would from a thread pool
    transcript.add_tool_results([
        ToolResultPart(call_id="c2", name="read", content="second"),
        ToolResultPart(call_id="c1", name="read", content="first"),
    ])
    assert [p.call_id for p in transcript.messages[-1].content] == ["c1", "c2"]


def test_unanswered_calls_rejected_by_check():
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([ToolCallPart(id="c1", name="read", arguments={})])
    with pytest.raises(TranscriptError, match="unanswered"):
        transcript.check_invariants()


# --- what pydantic now enforces on the objects themselves ---

def test_empty_message_cannot_be_constructed():
    with pytest.raises(ValidationError):
        Message(role="user", content=())


def test_empty_tool_call_id_cannot_be_constructed():
    with pytest.raises(ValidationError):
        ToolCallPart(id="", name="read", arguments={})


def test_unknown_field_is_rejected_not_ignored():
    with pytest.raises(ValidationError):
        TextPart(text="hi", txt="typo")           # extra="forbid"


def test_frozen_parts_cannot_be_mutated():
    part = TextPart(text="hi")
    with pytest.raises(ValidationError):
        part.text = "bye"


# --- serialization ---

def test_round_trips_through_json():
    original = Transcript()
    original.add_user_text("go")
    original.add_assistant([
        TextPart(text="checking"),
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
    ])
    original.add_tool_results([
        ToolResultPart(call_id="c1", name="read", content="x = 1"),
    ])

    restored = Transcript.model_validate_json(original.model_dump_json())
    assert restored == original                      # structural equality, whole tree


def test_discriminator_picks_the_right_class():
    restored = Message.model_validate_json(
        '{"role":"assistant","content":[{"type":"tool_call","id":"c1",'
        '"name":"read","arguments":{}}]}'
    )
    assert isinstance(restored.content[0], ToolCallPart)


def test_unknown_part_type_fails_loudly():
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Message.model_validate_json(
            '{"role":"user","content":[{"type":"video","url":"x"}]}'
        )
```

```bash
pytest tests/test_ch02_transcript.py -v
```

Ten pass. Three groups, and the middle one is new:

- The first three are the **cross-message** invariants — the ones you wrote by hand.
- The next four are **per-object** rules pydantic now enforces for you. Each of those was
  previously a bug you'd find at runtime, or a check you'd have to remember to write.
- The last three are **serialization**, which is now a round trip you can assert on
  rather than a loader you maintain.

`test_round_trips_through_json` is the one that quietly makes chapter 11 possible:
whole-transcript equality after a JSON round trip is what golden tests are.

---

Next: [03-provider-seam.md](03-provider-seam.md) — the one function the loop is allowed to call.
