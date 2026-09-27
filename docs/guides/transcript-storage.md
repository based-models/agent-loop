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
