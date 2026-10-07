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

`Agent.converse()` executes registered tools and calls `add_tool_results()` for you. Call it
yourself when you execute a pending tool call outside the agent loop or construct a transcript
manually:

```python
from agentloop import Transcript
from agentloop.transcript import ToolCallPart, ToolResultPart

transcript = Transcript()
transcript.add_user_message("Read a.py and b.py")
transcript.add_assistant_message(
    [
        ToolCallPart(
            id="call-a",
            tool_name="read_file",
            arguments={"path": "a.py"},
        ),
        ToolCallPart(
            id="call-b",
            tool_name="read_file",
            arguments={"path": "b.py"},
        ),
    ]
)

# Results may finish out of order. add_tool_results() puts them back into the
# model's original call order before appending the user-role result message.
transcript.add_tool_results(
    [
        ToolResultPart(
            call_id="call-b",
            tool_name="read_file",
            content="contents of b.py",
        ),
        ToolResultPart(
            call_id="call-a",
            tool_name="read_file",
            content="contents of a.py",
        ),
    ]
)

assert [part.call_id for part in transcript.messages[-1].content] == [
    "call-a",
    "call-b",
]
```

Every pending call must have exactly one result. A missing, unexpected, or duplicate call ID
raises `TranscriptError` instead of appending an invalid result message.

## Checking transcript invariants

`check_invariants()` validates the relationships that individual Pydantic models cannot
validate on their own. It checks that:

- tool call IDs are unique within a message;
- tool results immediately follow the assistant message that requested them;
- every requested call has exactly one result; and
- the transcript does not end with unanswered tool calls.

It returns `None` when the transcript is safe to send and raises `TranscriptError` when it is
not:

```python
from agentloop.errors import TranscriptError

try:
    transcript.check_invariants()
except TranscriptError as exc:
    print(f"Transcript is not sendable: {exc}")
```

Call it before sending a transcript assembled or loaded by your application. Deserialization
does not run this check automatically: a transcript captured midway through a tool turn is a
valid saved artifact even though it is not yet safe to send back to a model. Complete the
pending calls with `add_tool_results()` and then check it again.

See [transcript storage](transcript-storage.md) and the
[`transcript` API](../api/transcript.md).
