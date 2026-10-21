# Store transcripts

Use `to_jsonl()` to serialize a transcript and `from_jsonl()` to load it. These functions
work with text and lines; your application chooses where to store them. Each message
occupies one JSONL line.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from agentloop.transcript import TextPart, Transcript, from_jsonl, to_jsonl

transcript = Transcript()
transcript.add_user_message("Hello")
transcript.add_assistant_message((TextPart(text="Hi!"),))

with TemporaryDirectory() as directory:
    path = Path(directory) / "conversation.jsonl"
    path.write_text(to_jsonl(transcript.messages), encoding="utf-8")
    loaded = from_jsonl(path.read_text(encoding="utf-8").splitlines())
    print("JSONL:")
    print(path.read_text(encoding="utf-8"), end="")

loaded.check_invariants()
print("Same transcript:", loaded == transcript)
```

Output:

```text
JSONL:
{"role":"user","content":[{"type":"text","text":"Hello","citations":[]}],"origin":null}
{"role":"assistant","content":[{"type":"text","text":"Hi!","citations":[]}],"origin":null}
Same transcript: True
```

`to_jsonl()` includes a trailing newline. `from_jsonl()` skips blank lines but raises on
invalid or truncated JSON. It does not check conversation invariants, since a transcript
captured mid-turn may be valid to store but incomplete. Call `check_invariants()` before
sending a loaded transcript to a provider.

See the [`transcript` API](../api/transcript.md).

## Provider origin data

An assistant message may contain an `Origin`, which stores the provider, API, model, and
original response blocks. JSONL preserves this data so the same provider and API can replay
the turn in their original format, including details that the common message parts cannot
represent. Origin data can make the file large. When a different provider or API loads the
transcript, its adapter uses the common message parts instead.
