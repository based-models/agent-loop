# Conversations and transcripts

A `Transcript` holds a conversation as provider-independent messages. Use `converse()` when
you want to keep those messages and continue later. With OpenAI configured as in
[Hello World](../getting-started/hello-world.md):

```python
import os

from agentloop import Agent, Transcript, final_text

transcript = Transcript()
transcript.add_user_message("Explain recursion in one sentence.")

with Agent.from_env(os.environ, tools=[]) as agent:
    agent.converse(transcript)

print(final_text(transcript))
print(f"messages: {len(transcript.messages)}")
transcript.check_invariants()
```

Output:

```text
Recursion is a technique where a function solves a problem by calling a smaller version of itself until it reaches a simple base case that it can solve directly.
messages: 2
```

Messages contain typed parts such as text, model thinking, tool calls, and tool results.
`Agent.converse()` runs registered tools and adds their results to the transcript for you.

## Add tool results yourself

Call `add_tool_results()` when you execute a pending tool call outside the agent loop or
construct a transcript manually. Results may arrive out of order; the method places them
in the model's original call order:

```python
from agentloop import Transcript
from agentloop.transcript import ToolCallPart, ToolResultPart

transcript = Transcript()
transcript.add_user_message("Look up A and B.")
transcript.add_assistant_message([
    ToolCallPart(id="call-a", tool_name="lookup", arguments={"key": "A"}),
    ToolCallPart(id="call-b", tool_name="lookup", arguments={"key": "B"}),
])
transcript.add_tool_results([
    ToolResultPart(call_id="call-b", tool_name="lookup", content="Result B"),
    ToolResultPart(call_id="call-a", tool_name="lookup", content="Result A"),
])

print([part.call_id for part in transcript.messages[-1].content])
transcript.check_invariants()
```

Output:

```text
['call-a', 'call-b']
```

Every pending call needs exactly one result. A missing, unexpected, or duplicate call ID
raises `TranscriptError` before an invalid result message is added.

## Check transcript invariants

`check_invariants()` verifies that tool-call IDs are unique, results immediately follow
their calls, every call has one result, and no calls remain unanswered. It returns `None`
when the transcript is safe to send and raises `TranscriptError` otherwise:

```python
from agentloop import Transcript
from agentloop.errors import TranscriptError
from agentloop.transcript import ToolCallPart, ToolResultPart

transcript = Transcript()
transcript.add_user_message("Look up A.")
transcript.add_assistant_message([
    ToolCallPart(id="call-a", tool_name="lookup", arguments={"key": "A"}),
])

try:
    transcript.check_invariants()
except TranscriptError as error:
    print(error)

transcript.add_tool_results([
    ToolResultPart(call_id="call-a", tool_name="lookup", content="Result A"),
])
transcript.check_invariants()
print("transcript is sendable")
```

Output:

```text
message 1 ends with unanswered tool calls ['call-a']; the transcript is not sendable
transcript is sendable
```

Call this check before sending a transcript assembled or loaded by your application.
Deserialization does not run it automatically: a saved transcript may legitimately end
mid-turn, before a tool has returned. Complete pending calls with `add_tool_results()` and
then check again.

See [transcript storage](transcript-storage.md) and the
[`transcript` API](../api/transcript.md).
