# Store transcripts

Serialization is deliberately separate from filesystem policy. `to_jsonl()` returns text,
and `from_jsonl()` accepts any iterable of lines.

```python
from pathlib import Path

from agentloop.transcript import from_jsonl, to_jsonl

path = Path("conversation.jsonl")
path.write_text(to_jsonl(transcript.messages), encoding="utf-8")

loaded = from_jsonl(path.read_text(encoding="utf-8").splitlines())
loaded.check_invariants()
```

The serializer writes one `Message` per line and includes a trailing newline. Blank input
lines are ignored. Invalid or truncated JSON raises instead of silently discarding data.

`from_jsonl()` does not check conversation invariants because a legitimate capture may end
mid-turn. Call `check_invariants()` before sending a loaded transcript to a provider.

See the [`transcript` API](../api/transcript.md).

## `Message.origin`

An assistant `Message` may carry an `Origin`: the provider's own wire form of that turn
(`provider`, `api`, `model`, `raw`). Adapters replay it instead of rebuilding the turn from the
canonical parts, which keeps things the canonical form cannot express, such as Anthropic's
encrypted search results and signed thinking, or OpenRouter's `reasoning_details`.

It can be large. JSONL keeps it so a resumed conversation replays exactly. Drop it
(`message.model_copy(update={"origin": None})`) if you accept a replay rebuilt from canonical
parts, or switch providers: an origin from a different provider or API is ignored on replay.
