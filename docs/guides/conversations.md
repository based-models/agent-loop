# Conversations and transcripts

The transcript is the canonical, provider-independent conversation history.

```python
from agentloop import Transcript, final_text

transcript = Transcript()
transcript.add_user_message("Explain recursion in one sentence.")
agent.converse(transcript)

print(final_text(transcript))
transcript.check_invariants()
```

Messages contain typed parts: text, model thinking, tool calls, and tool results. Tool
results must immediately answer every call in the preceding assistant message. Use
`add_tool_results()` rather than appending result messages manually; it verifies identifiers
and restores call order.

`check_invariants()` is useful before sending a transcript assembled or loaded by your
application. A transcript captured midway through a tool turn can be loaded, but is not yet
sendable.

See [transcript storage](transcript-storage.md) and the
[`transcript` API](../api/transcript.md).
