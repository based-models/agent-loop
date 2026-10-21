# `agentloop.loop`

The provider-independent reason/act/observe loop. These functions are the lower-level API:
they run the full agent loop, while you assemble the client, tool registry, and dependencies
yourself. [`Agent`](agent.md) is the convenience API that assembles those pieces for you.

## Constants

- `DEFAULT_MAX_TOKENS = 16_000`
- `MAX_TOKENS_CEILING = 64_000`
- `DEFAULT_MAX_ITERATIONS = 50`
- `DEFAULT_DEADLINE_SECONDS = 600.0`

## `run_conversation`

```text
run_conversation(
    transcript,
    client,
    registry,
    deps,
    *,
    system=None,
    max_tokens=16_000,
    max_iterations=50,
    deadline_seconds=600.0,
    observer=None,
    hosted_tools=(),
    agent_name=None,
    conversation_id=None,
    correlation_id=None,
    parent_id=None,
    summary_options=None,
) -> Transcript
```

Updates and returns the supplied `Transcript`. Each iteration sends a `ModelRequest` to
the client. The loop adds the model's response to the transcript, runs any requested local
tools, and adds their results before the next iteration.

If a response ends partway through a tool call or before producing readable content, the
loop discards that response and retries with twice the token allowance, up to the 64,000
ceiling. It never lowers a larger allowance supplied by the caller. If the allowance
cannot grow, it raises `LoopBudgetExceeded` with `kind="max_tokens"`.

A provider that pauses a turn (`StopReason.PAUSED`, for example Anthropic's `pause_turn` while
a hosted tool runs) is continued without sending a user message: the continuation joins the
same assistant message, and each continuation counts as an iteration.

If `transcript` ends with tool calls that have no results, the loop runs them before the
first model call. It then checks the transcript for invalid message order and raises
`TranscriptError` if necessary.

The deadline is checked before each model call. A provider call, its retries, or a started
tool batch may finish after it. `None` disables the deadline. Exhaustion raises
`LoopBudgetExceeded`; provider errors propagate unchanged.

`agentloop` supports Anthropic, OpenAI, OpenRouter, and Modal/vLLM. This example uses
OpenAI. Install its support and set the provider credentials:

```bash
python -m pip install 'based-models-agentloop[openai-compat]'
export LLM_PROVIDER=openai
export OPENAI_API_KEY='your-key'
```

The example creates each component explicitly and records the model request and response:

```python
import os

from agentloop import (
    Deps, ListObserver, Registry, Settings, Transcript,
    build_client, final_text, run_conversation,
)

transcript = Transcript()
transcript.add_user_message("How many days are in a week? Reply with one digit.")
observer = ListObserver()
client = build_client(Settings.from_env(os.environ))
try:
    run_conversation(transcript, client, Registry([]), Deps(), observer=observer)
    print(final_text(transcript))
    print(observer.kinds())
finally:
    client.close()
```

Output:

```text
7
['request', 'response']
```

`Registry([])` supplies no local tools, and `Deps()` supplies the default dependency
bundle. `final_text()` reads the last assistant answer from the updated transcript.

## `run_conversation_result`

Accepts the same parameters as `run_conversation` and returns a `ConversationResult` with
`.transcript`, `.summary`, and `.text`. The summary records the conversation's status and
usage. A completed answer, refusal, or truncated final answer returns normally; a budget
or provider failure raises an exception with its summary attached as `.summary`. The agent
and conversation identifiers label the summary. `summary_options` configures the timeline,
repeated-call detection, and limit warnings. See
[Conversation summaries](../guides/summaries.md).

## `ListObserver`

An observer for tests and debugging. The example above passes one to
`run_conversation()`. Its public `events` list contains ordered `(kind, payload)` tuples
for requests, responses, retries, and local tool starts and ends. `kinds() -> list[str]`
returns just the event names.
