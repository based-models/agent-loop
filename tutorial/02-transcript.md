# 02 — The transcript

**What you build in this chapter**

| File | What goes in it |
|---|---|
| `scratch/capture_anthropic.py` | A throwaway script to look at a real Anthropic response |
| `scratch/capture_openrouter.py` | The same, for an OpenAI-compatible provider |
| `src/harness/errors.py` | Two exception types (the file grows in later chapters) |
| `src/harness/transcript.py` | The canonical message types — the core of the whole project |
| `tests/test_transcript.py` | The checkpoint |

**The order matters.** You are not going to write a data model and then be told what it's
for. You are going to look at what two providers actually send back, notice where they
disagree, and then design a type that holds both. Every field in `transcript.py` will be
traceable to something you saw with your own eyes in Part 1.

---

# Part 1 — Look at what the providers actually send

## 1.1 Set up a scratch directory

These scripts are for looking at things, not for shipping. Keep them out of the package
and out of git.

```bash
mkdir -p scratch
echo "scratch/" >> .gitignore
```

## 1.2 Capture an Anthropic tool-use turn

**Create `scratch/capture_anthropic.py`:**

```python
"""Print a real Anthropic response so we can see its shape.

Run:  ANTHROPIC_API_KEY=sk-ant-... python scratch/capture_anthropic.py
"""

import json

from anthropic import Anthropic

client = Anthropic()

READ_TOOL = {
    "name": "read",
    "description": "Read a file from the working directory.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file."}},
        "required": ["path"],
        "additionalProperties": False,
    },
}

response = client.messages.create(
    model="claude-opus-5",
    max_tokens=2000,
    tools=[READ_TOOL],
    messages=[{"role": "user", "content": "What does main.py do? Read it first."}],
)

# model_dump() turns the SDK objects into plain dicts so we can see everything.
print(json.dumps(response.model_dump(), indent=2, default=str))
```

Run it:

```bash
conda activate harness
ANTHROPIC_API_KEY=sk-ant-... python scratch/capture_anthropic.py
```

> **No key yet?** Read on — the output below is annotated, and nothing in Part 2 requires
> you to have run it. But run it when you can. Seeing your own trace is the difference
> between knowing that `signature` is a long opaque string and *believing* it.

### What comes back

Abridged, with the parts that matter kept in full:

```jsonc
{
  "id": "msg_01XyZ...",
  "type": "message",
  "role": "assistant",
  "model": "claude-opus-5",
  "content": [                          // <-- A LIST. Not a string.
    {
      "type": "thinking",
      "thinking": "",                   // <-- EMPTY. See note 2.
      "signature": "ErUBCkYIBRgCKkBw3k9Zv...(hundreds of chars)...=="
    },
    {
      "type": "text",
      "text": "Let me read that file."
    },
    {
      "type": "tool_use",
      "id": "toolu_01A09q90qw90lq917835lq9",
      "name": "read",
      "input": {"path": "main.py"}      // <-- ALREADY A DICT.
    }
  ],
  "stop_reason": "tool_use",
  "usage": {
    "input_tokens": 431,
    "output_tokens": 87,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0
  }
}
```

*(Structure and field names are from the Messages API; your ids, token counts and
signature will differ. If your `content` has no `thinking` block, see note 2.)*

**Three things to notice. Each one becomes a design decision in Part 2.**

**Note 1 — `content` is an ordered list of typed blocks.** Not a string. The assistant
said something *and* called a tool, and the list preserves which came first. A turn can
be `[text, tool_use, text]`, or two `tool_use` blocks in a row when the model wants two
files at once.

**Note 2 — the thinking block has an empty `thinking` and a huge `signature`.** This
surprises everyone. Thinking is on by default on Opus 5, and `display` defaults to
`"omitted"` — so you get the block, with its signature, but no readable reasoning. The
signature is the payload. **You must send these blocks back unchanged** when you continue
the conversation on the same model; on newer models there is a *preserved thinking* check
that rejects a request whose earlier turns were edited.

That single fact is why the transcript in Part 3 is append-only.

**Note 3 — `input` is already a parsed object.** `{"path": "main.py"}`, a real dict. No
JSON string, no parsing step. Remember this — the next provider does it differently.

## 1.3 Capture the same turn from an OpenAI-compatible provider

**Create `scratch/capture_openrouter.py`:**

```python
"""Print a real OpenRouter response. Same conversation, different wire format.

Run:  OPENROUTER_API_KEY=sk-or-... python scratch/capture_openrouter.py
"""

import json
import os

import httpx

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file from the working directory.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to the file."}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
}

response = httpx.post(
    "https://openrouter.ai/api/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
    json={
        "model": "openrouter/free",
        "max_tokens": 2000,
        "tools": [READ_TOOL],
        "messages": [
            {"role": "user", "content": "What does main.py do? Read it first."},
        ],
    },
    timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
)
print(json.dumps(response.json(), indent=2))
```

### What comes back

```jsonc
{
  "id": "gen_01H...",
  "model": "some-free-model",
  "choices": [
    {
      "finish_reason": "tool_calls",
      "message": {
        "role": "assistant",
        "content": "Let me read that file.",   // <-- A STRING (or null).
        "tool_calls": [                         // <-- A SIBLING ARRAY.
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "read",
              "arguments": "{\"path\": \"main.py\"}"   // <-- A JSON *STRING*.
            }
          }
        ]
      }
    }
  ],
  "usage": {"prompt_tokens": 388, "completion_tokens": 54}
}
```

**Three things to notice.**

**Note 4 — text and tool calls are siblings, not a sequence.** `content` is a string;
`tool_calls` is a separate array. There is **no way to express** "text, then a tool call,
then more text" in this shape. The ordering information from Note 1 simply has nowhere to
go.

**Note 5 — `arguments` is a JSON-encoded string.** You must `json.loads` it. And it can
be malformed: vLLM's own documentation warns that when tool calls are extracted from raw
text, "arguments may occasionally be malformed or violate the function's parameter
schema."

**Note 6 — no reasoning field here at all**, and `usage` has different key names
(`prompt_tokens`, not `input_tokens`) and no cache counters.

## 1.4 Put them side by side

| | Anthropic | OpenAI-compatible |
|---|---|---|
| Assistant content | ordered list of blocks | `content` string **+** sibling `tool_calls` |
| Text/tool-call ordering | preserved | **unrepresentable** |
| Tool arguments | parsed `dict` | JSON **string**, may be malformed |
| Tool call id | `toolu_01A09…` | `call_abc123` |
| Reasoning | `thinking` block with a `signature` that must be replayed | absent, or a separate `reasoning_details` |
| Why it stopped | `stop_reason: "tool_use"` | `finish_reason: "tool_calls"` |
| Token counts | `input_tokens` / `output_tokens` | `prompt_tokens` / `completion_tokens` |

### The decision this forces

You need one internal shape that both of these convert into. There are two candidates,
and the table above picks the winner for you:

**Anthropic's shape can hold everything OpenAI's can.** An ordered list of blocks can
represent `[text, tool_call]` — that's just a list with two entries.

**OpenAI's shape cannot hold everything Anthropic's can.** Give it `[text, tool_call,
text]` and you must either drop a block or mash the two texts together. Give it a
thinking block with a signature and there's no field for it.

So: **the canonical shape is block-structured, like Anthropic's.** Converting *into* it
loses nothing. Converting *out of* it, to OpenAI, loses the ordering — but that happens
once, at the edge, in one function you can point at.

> **Why not just use `anthropic.types.Message` directly, then?**
> Four reasons, and the first is the one that bites soonest.
> 1. Every fake response in every test becomes twelve lines of SDK ceremony. You are
>    about to write tests that build these by hand constantly.
> 2. It makes `anthropic` a mandatory install for someone who only uses Modal. Chapter 1
>    made it an optional extra; that only holds if core never imports it.
> 3. SDK types change between versions, and your saved transcripts change with them.
> 4. New block types keep appearing in that union. Owning your types means a new one
>    breaks exactly one function in one adapter.

---

# Part 2 — Derive the model, one field at a time

Now build the type. Each part below exists to hold something you saw in Part 1.

## 2.1 `TextPart` — the easy one

Note 1 showed `{"type": "text", "text": "Let me read that file."}`.

```python
class TextPart(Part):
    type: Literal["text"] = "text"
    text: str
```

## 2.2 `ToolCallPart` — three fields you wouldn't have guessed

Anthropic gave you `{"id": "toolu_…", "name": "read", "input": {"path": "main.py"}}`.
OpenRouter gave you `{"id": "call_abc123", "function": {"name": "read", "arguments":
"{\"path\": \"main.py\"}"}}`.

Same information, two encodings. Pick one for the inside:

```python
class ToolCallPart(Part):
    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]          # ALWAYS parsed — never a JSON string
    raw_arguments: str | None = None
    parse_error: str | None = None
```

**`arguments` is a `dict`, decided by Note 5.** If the canonical type were a string,
every single function downstream would have to handle a JSON decode failure. Parse once,
in the adapter, and the rest of the program never thinks about it again.

**`id` is opaque and never touched.** You saw two formats (`toolu_01A09…`,
`call_abc123`); there are others. The loop never parses one, builds one, or rewrites one
— it hands back whatever the provider sent. `min_length=1` because some servers omit the
id entirely, and the adapter has to mint one; this makes forgetting that a loud failure.

**`raw_arguments` and `parse_error` exist because of the malformed case in Note 5.** When
`json.loads` fails you have a problem: you can't drop the tool call (Part 4 explains why
that's fatal), and you can't invent arguments. So you keep the part, record *why* it
failed, and keep the original string:

- `parse_error` set → chapter 6 turns it into an error result the model can read and retry.
- `raw_arguments` → when you replay this turn to an OpenAI-compatible server, you send
  back the **exact bytes the model produced**, not `"{}"`. Rewriting history to something
  the model never said confuses it, and on Anthropic it would invalidate a signature.

## 2.3 `ToolResultPart` — the other half of the pair

Nothing in Part 1 showed this, because it's what *you* send back. The shape follows from
`ToolCallPart`:

```python
class ToolResultPart(Part):
    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)   # == the ToolCallPart.id it answers
    name: str
    content: str
    is_error: bool = False
```

`call_id` is the link. Part 4 is entirely about keeping it honest.

`is_error` rather than raising: a tool that fails should *tell the model it failed*, so
the model can try something else. Chapter 6 builds on this.

## 2.4 `ThinkingPart` — now Note 2 pays off

Go back and look at what you captured: a block with `"thinking": ""` and a `signature`
hundreds of characters long. Every field below exists to get that block back to the API
unchanged.

```python
class ThinkingPart(Part):
    type: Literal["thinking"] = "thinking"
    text: str                          # usually "" — display defaults to "omitted"
    signature: str | None = None       # the opaque blob. Must survive the round trip.
    provider: str | None = None        # who minted it
    model: str | None = None           # which model minted it
    raw: Any = None                    # the whole original block, verbatim
```

**`text` is usually empty**, and that is not a bug — it's Note 2. Don't be tempted to
drop the part because it "has no content"; the signature is the content.

**`signature` must be echoed back byte-for-byte.** This is the field that makes the whole
type necessary.

**`raw` holds the entire original block.** You are not the authority on which fields that
signature covers, so you don't reconstruct the block from named fields — you keep it and
send it back whole.

**`provider` and `model` gate replay**, because the rules differ:

| Provider | Replay rule |
|---|---|
| Anthropic | Send it back verbatim, signature included |
| OpenRouter | Send `reasoning_details` back verbatim — **only if** provider and model are unchanged |
| Modal / vLLM | Drop it. `reasoning_content` is generally not replayable and most chat templates discard it |

Those two fields are what let chapter 5 ask "did the same model produce this?" instead of
guessing.

## 2.5 The union, and why every part carries `type`

You now have four classes. A message's content is a list of *any* of them:

```python
type ContentPart = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]
```

> **Python note.** `type X = ...` is Python 3.12's way of naming a type (PEP 695). Older
> code writes `X: TypeAlias = ...`; both work, and ruff will nudge you to the new one on
> 3.12. `Annotated[SomeType, extra]` attaches metadata to a type — here, pydantic's
> instruction about which field to discriminate on.

That `type` field on every part — which looked redundant when you wrote `TextPart` — is
what makes this work. `Field(discriminator="type")` tells pydantic: read `type`, go
straight to the matching class.

Without a discriminator, pydantic tries all four classes and keeps whichever validates,
which is slower and gives terrible errors. With it, a bad part fails as
`Input tag 'video' found using 'type' does not match any of the expected tags`.

It is also what makes Part 5's one-line save-and-load possible.

## 2.6 `Message` — and two roles that don't exist

```python
class Message(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Role
    content: tuple[ContentPart, ...] = Field(min_length=1)
```

with

```python
type Role = Literal["user", "assistant"]
```

**Two roles you'd expect are missing, deliberately.**

**No `"system"`.** The system prompt is a field on the *request* (chapter 3), not a
message. Anthropic takes it as a top-level parameter anyway — look at your capture script
again, `system=` would have been a keyword argument, not an entry in `messages`. And many
vLLM chat templates raise an exception if a system message appears twice or isn't first.
By keeping it off `Role`, sending two is not something you can do.

**No `"tool"`.** OpenAI wants N separate `{"role": "tool"}` messages. Anthropic wants one
user message containing N `tool_result` blocks. We store the Anthropic grouping, because
it encodes the thing we need to enforce: *these N results answer the N calls from that one
assistant turn.* Fanning one message out into N is a loop. Merging N back into one needs
lookahead.

**`content` is a `tuple`, not a `list`.** `frozen=True` stops you reassigning the field,
but would happily let you `.append()` to a list inside it. A tuple can't be appended to.

**`Field(min_length=1)`** means an empty message cannot be built at all. Anthropic rejects
empty text blocks and vLLM templates break on them, so this is a real API rule moved into
the type.

---

# Part 3 — Write the files

## File: `src/harness/errors.py`

Two exception types now; chapters 3 and 6 add more to this same file.

```python
"""Every exception this library raises."""


class HarnessError(Exception):
    """Base for everything this library raises."""


class TranscriptError(HarnessError):
    """The transcript is in a state no provider will accept."""
```

One base class means a caller can write `except HarnessError:` and catch everything of
ours and nothing of anyone else's.

## File: `src/harness/transcript.py`

Everything from Part 2, in one file. This is the complete contents:

```python
"""The canonical conversation types.

Every provider's wire format converts into these; nothing in here knows which
provider it came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.errors import TranscriptError

type Role = Literal["user", "assistant"]


class Part(BaseModel):
    """Base for every content part: immutable, and rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TextPart(Part):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(Part):
    """Reasoning emitted by the model.

    `text` is usually "" (providers omit the readable trace by default). The
    `signature` is the part that matters: it must be replayed byte-for-byte.
    """

    type: Literal["thinking"] = "thinking"
    text: str
    signature: str | None = None
    provider: str | None = None
    model: str | None = None
    raw: Any = None


class ToolCallPart(Part):
    """A tool call the model made.

    `arguments` is ALWAYS a parsed dict. When a provider sends a malformed JSON
    string, `parse_error` is set and `raw_arguments` keeps the original bytes.
    """

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any]
    raw_arguments: str | None = None
    parse_error: str | None = None


class ToolResultPart(Part):
    """Our answer to one ToolCallPart. `call_id` must match that call's `id`."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    name: str
    content: str
    is_error: bool = False


type ContentPart = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: tuple[ContentPart, ...] = Field(min_length=1)


class Transcript(BaseModel):
    """The running conversation. Append-only — see Part 4."""

    messages: list[Message] = Field(default_factory=list)

    # --- the only three ways to change it ---

    def add_user_text(self, text: str) -> None:
        self.messages.append(Message(role="user", content=(TextPart(text=text),)))

    def add_assistant(self, parts: Sequence[ContentPart]) -> None:
        self.messages.append(Message(role="assistant", content=tuple(parts)))

    def add_tool_results(self, results: Sequence[ToolResultPart]) -> None:
        """Append results for the pending calls. Raises if they don't match."""
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

    # --- queries ---

    def pending_tool_calls(self) -> tuple[ToolCallPart, ...]:
        """Tool calls in the last assistant message; () if there are none."""
        if not self.messages:
            return ()
        last = self.messages[-1]
        if last.role != "assistant":
            return ()
        return tuple(p for p in last.content if isinstance(p, ToolCallPart))

    def check_invariants(self) -> None:
        """Raise TranscriptError if this transcript is not safe to send."""
        for index, message in enumerate(self.messages):
            if message.role != "assistant":
                continue
            calls = [p for p in message.content if isinstance(p, ToolCallPart)]
            if not calls:
                continue

            ids = [call.id for call in calls]
            if len(set(ids)) != len(ids):
                raise TranscriptError(
                    f"message {index} has duplicate tool_call ids: {ids}"
                )

            if index + 1 >= len(self.messages):
                raise TranscriptError(
                    f"message {index} ends with unanswered tool calls {ids}; "
                    "the transcript is not sendable"
                )
            answered = {
                p.call_id
                for p in self.messages[index + 1].content
                if isinstance(p, ToolResultPart)
            }
            if answered != set(ids):
                raise TranscriptError(
                    f"message {index} calls {sorted(set(ids))} "
                    f"but message {index + 1} answers {sorted(answered)}"
                )


def append_jsonl(path: Path, messages: Sequence[Message]) -> None:
    """Append messages to a JSONL log, one per line. See Part 5."""
    with path.open("a") as handle:
        for message in messages:
            handle.write(message.model_dump_json() + "\n")


def load_jsonl(path: Path) -> Transcript:
    """Read a JSONL log back into a Transcript. See Part 5."""
    return Transcript(
        messages=[
            Message.model_validate_json(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]
    )
```

Part 4 explains the two `Transcript` methods that aren't obvious, and Part 5 explains the
two functions at the bottom.

---

# Part 4 — Why append-only, and why the pairing check exists

## The error this prevents

Drop one tool result and your **next** request fails with:

```
400 `tool_use` ids were found without `tool_result` blocks immediately after:
toolu_01Abc... Each `tool_use` block must have a corresponding `tool_result`
block in the next message.
```

Note where that arrives: on the request *after* the mistake, from a remote server, naming
an id you've never seen in your own code. The cause might be an exception in a tool
handler thirty seconds earlier.

`add_tool_results` turns that into a `TranscriptError` at the line that caused it, with no
network round trip. That is the entire reason it's a method and not
`transcript.messages.append(...)`.

The reference course does not have this, and pays for it: `decode` carries a
`_heal_dangling_tool_calls()` that scans for unpaired calls and synthesizes fake
"interrupted" results to un-brick a session. A reasonable repair for a state it can reach
— and a state we've made unreachable.

## Why `check_invariants` is a method, not a validator

The obvious pydantic move is `@model_validator`, so the transcript checks itself on every
change. **Don't.** A correct transcript is temporarily invalid halfway through a turn:

```
append assistant message with 2 tool calls   <-- now "invalid": calls unanswered
run the tools                                 <-- still "invalid"
append the results                            <-- valid again
```

A validator firing on mutation would make the normal path impossible.

The rule to carry through the rest of the tutorial:

> **Pydantic validates what must always be true of one object.** A message has at least
> one part; a tool call id is non-empty.
>
> **`check_invariants` validates what must be true *between* objects**, at the moments
> you're about to send.

Two of the five rules you might have written by hand are already gone, absorbed into the
types:

| Rule | Enforced by |
|---|---|
| No empty messages | `Field(min_length=1)` on `Message.content` |
| No empty tool call ids | `Field(min_length=1)` on `ToolCallPart.id` |
| Ids unique within a turn | `check_invariants` |
| Calls are followed by results | `check_invariants` |
| Results match the calls exactly | `add_tool_results` **and** `check_invariants` |

## Why append-only

Note 2 again: Anthropic's preserved-thinking check rejects a request whose earlier turns
were edited. So there is no `pop`, no `__setitem__`, no `truncate` on `Transcript`.

When you reach compaction (chapter 12), the move is to build a **new** transcript, not to
edit this one.

---

# Part 5 — Saving and loading, for free

The discriminator from 2.5 earns its keep here. The entire session-log implementation:

```python
path.write_text(transcript.model_dump_json(indent=2))
transcript = Transcript.model_validate_json(path.read_text())
```

No serializer, no loader, no `match` on `part["type"]`. And a corrupted file fails with a
message naming the exact field instead of producing a `Transcript` full of dicts that
breaks somewhere later.

For an append-only JSONL log — one message per line, so a crashed run still leaves a
readable file — `append_jsonl` and `load_jsonl` are already at the bottom of the file you
wrote in Part 3. Both are four lines, and both are just the two calls above in a loop.

Chapter 9's `--dump-transcript` uses these; chapter 11's golden tests diff
`model_dump_json()` output.

One caveat: `ThinkingPart.raw` is typed `Any`, so pydantic passes it through without
validating it. It's JSON-serializable for all three providers; if you ever store something
that isn't, this is where you find out.

---

# Checkpoint

## File: `tests/test_transcript.py`

```python
import pytest
from pydantic import ValidationError

from harness.errors import TranscriptError
from harness.transcript import (
    Message,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultPart,
    Transcript,
)

# --- the cross-message rules (Part 4) ---


def test_missing_tool_result_is_caught_locally() -> None:
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


def test_results_are_reordered_to_call_order() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([
        ToolCallPart(id="c1", name="read", arguments={}),
        ToolCallPart(id="c2", name="read", arguments={}),
    ])
    # results arrive out of order, as they will from a thread pool in chapter 6
    transcript.add_tool_results([
        ToolResultPart(call_id="c2", name="read", content="second"),
        ToolResultPart(call_id="c1", name="read", content="first"),
    ])
    results_message = transcript.messages[-1]
    call_ids = [p.call_id for p in results_message.content
                if isinstance(p, ToolResultPart)]
    assert call_ids == ["c1", "c2"]


def test_unanswered_calls_are_rejected_by_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([ToolCallPart(id="c1", name="read", arguments={})])
    with pytest.raises(TranscriptError, match="unanswered"):
        transcript.check_invariants()


def test_a_complete_turn_passes_check_invariants() -> None:
    transcript = Transcript()
    transcript.add_user_text("go")
    transcript.add_assistant([ToolCallPart(id="c1", name="read", arguments={})])
    transcript.add_tool_results([
        ToolResultPart(call_id="c1", name="read", content="x = 1"),
    ])
    transcript.check_invariants()          # does not raise


# --- the per-object rules pydantic now enforces ---

def test_an_empty_message_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        Message(role="user", content=())


def test_an_empty_tool_call_id_cannot_be_built() -> None:
    with pytest.raises(ValidationError):
        ToolCallPart(id="", name="read", arguments={})


def test_a_typo_is_an_error_not_a_silent_drop() -> None:
    with pytest.raises(ValidationError):
        # mypy catches this statically too, which is the point — the ignore is
        # what lets us also prove the RUNTIME behaviour.
        TextPart(text="hi", txt="typo")  # type: ignore[call-arg]


def test_parts_are_immutable() -> None:
    part = TextPart(text="hi")
    with pytest.raises(ValidationError):
        part.text = "bye"


# --- round trip (Part 5) ---

def test_a_whole_transcript_survives_json() -> None:
    original = Transcript()
    original.add_user_text("go")
    original.add_assistant([
        ThinkingPart(text="", signature="ErUBCkYIBRgC...", provider="anthropic",
                     model="claude-opus-5", raw={"type": "thinking"}),
        TextPart(text="checking"),
        ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
    ])
    original.add_tool_results([
        ToolResultPart(call_id="c1", name="read", content="x = 1"),
    ])

    restored = Transcript.model_validate_json(original.model_dump_json())
    assert restored == original


def test_the_discriminator_rebuilds_the_right_class() -> None:
    restored = Message.model_validate_json(
        '{"role":"assistant","content":[{"type":"tool_call","id":"c1",'
        '"name":"read","arguments":{}}]}'
    )
    assert isinstance(restored.content[0], ToolCallPart)


def test_an_unknown_part_type_fails_loudly() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Message.model_validate_json(
            '{"role":"user","content":[{"type":"video","url":"x"}]}'
        )
```

## Run it

```bash
conda activate harness
pytest tests/test_transcript.py -v
mypy
ruff check .
```

Expected: **11 passed**, `Success: no issues found in 6 source files`, `All checks passed!`

Two things that will bite if you skip them:

- **`mypy --strict` wants `-> None` on every test function.** Tedious, and the
  alternative is worse — a test that silently returns something is a test that silently
  does nothing.
- **One test needs `# type: ignore[call-arg]`.** `TextPart(text="hi", txt="typo")` is
  caught by mypy *and* by pydantic at runtime. The ignore suppresses the static error so
  the test can prove the runtime one. Two different safety nets, and you want both.

If `test_a_whole_transcript_survives_json` passes, you have the thing chapter 11's golden
tests are built on — a conversation that survives a save/load round trip and compares
equal.

---

**What you have now:** a conversation type that can hold anything either provider sends,
that makes the most common fatal mistake unrepresentable, and that saves and loads in one
line. Nothing in it knows what a provider is.

Next: [03-provider-seam.md](03-provider-seam.md) — the one function the loop is allowed to call.
