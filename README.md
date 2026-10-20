# based-models-agentloop

`based-models-agentloop` is a synchronous Python library for building reliable agents that
use large language models and tools. The same application code can work with Anthropic,
OpenAI, OpenRouter, or Modal/vLLM. The library manages conversations, tool execution,
retries, limits, and operational reporting while your application keeps control of prompts,
data, approvals, and secrets.

The package is installed as `based-models-agentloop` and imported as `agentloop`.

## Why use agentloop?

- Use the same agent code with Anthropic, OpenAI, OpenRouter, or Modal/vLLM.
- Run local Python tools or provider-hosted web search.
- Keep complete, typed, serializable transcripts—including tool activity, sources, and
  citations.
- Execute independent tool calls concurrently while preserving their original result order.
- Get consistent retries, errors, usage data, time limits, and iteration limits across
  providers.
- Record outcomes, event logs, distributed traces, and metrics without changing agent
  behavior.
- Test agent behavior offline with the included scripted `FakeClient`.
- Install only the provider dependencies your application needs.

Requires Python 3.12 or newer.

## Quick start

Install the extra for your provider. For example, to use OpenAI:

```bash
pip install "based-models-agentloop[openai-compat]"
export LLM_PROVIDER=openai
export OPENAI_API_KEY="..."
```

Then create and run an agent:

```python
import os

from agentloop import Agent

with Agent.from_env(
    os.environ,
    tools=[],
    system="Answer clearly and concisely.",
) as agent:
    print(agent.run("Why is the sky blue?"))
```

`Agent.from_env()` receives an environment mapping explicitly—the library never reads the
process environment or loads a `.env` file on its own. The context manager closes the client
created by `from_env()` when the block exits.

## Installation options

| Extra | Adds |
|---|---|
| `anthropic` | The Anthropic SDK |
| `openai-compat` | `httpx` support for OpenAI, OpenRouter, and Modal/vLLM |
| `otel` | The OpenTelemetry SDK and OTLP HTTP exporter |
| `all` | Every optional integration above |

```bash
pip install "based-models-agentloop[anthropic]"
pip install "based-models-agentloop[openai-compat]"
pip install "based-models-agentloop[otel]"
pip install "based-models-agentloop[all]"
```

Provider dependencies are imported lazily, so importing `agentloop` does not require every
optional SDK.

## Providers

Configuration selects the provider; application and tool code stay the same.

| Provider | API | Local tools | Hosted web search |
|---|---|---:|---:|
| Anthropic | Messages | Yes | Yes |
| OpenAI | Responses (default) | Yes | Yes |
| OpenAI | Chat Completions compatibility | Yes | No |
| OpenRouter | OpenAI-compatible Chat Completions | Yes | No |
| Modal/vLLM | OpenAI-compatible Chat Completions | Yes | No |

Each provider integration converts requests, responses, token usage, stop reasons,
transcripts, and failures into the same Python types. OpenAI uses the Responses API by
default; set
`OPENAI_API=completions` only when Chat Completions compatibility is required.

## Local Python tools

The `@tool` decorator pairs a Python handler with an explicit JSON Schema. The schema is the
contract shown to the model and is never inferred from the function signature.

```python
import os

from agentloop import Agent, ToolError, tool

ADD_SCHEMA = {
    "type": "object",
    "properties": {
        "left": {"type": "integer"},
        "right": {"type": "integer"},
    },
    "required": ["left", "right"],
    "additionalProperties": False,
}

@tool(parameters=ADD_SCHEMA)
def add(left: int, right: int) -> str:
    """Add two integers."""
    if abs(left) > 1_000_000 or abs(right) > 1_000_000:
        raise ToolError("numbers must be at most one million")
    return str(left + right)

with Agent.from_env(
    os.environ,
    tools=[add],
    system="Use the add tool for arithmetic.",
) as agent:
    print(agent.run("What is 27 plus 15?"))
```

`ToolError` returns safe feedback to the model. Unexpected exceptions are logged and
converted into error results so every tool call still receives a matching result.

Subclass `Deps` when tools need trusted application state such as a database client, tenant
identifier, or request context. A `before_tool` approval hook can allow or deny each call
before execution. Independent calls run concurrently, up to eight at a time, while results
retain the model's original call order.

## Provider-hosted web search

Hosted tools run inside the model provider rather than in your Python process. Native web
search is supported by Anthropic and by OpenAI's Responses API:

```python
import os

from agentloop import Agent, HostedTool

search = HostedTool(
    kind="web_search",
    options={
        "allowed_domains": ["europa.eu"],
        "search_context_size": "high",
    },
)

with Agent.from_env(os.environ, tools=[search]) as agent:
    print(agent.run("What changed in EU AI Act guidance this month?"))
```

Search activity, consulted sources, and citations are preserved in the transcript. Passing a
hosted tool to an unsupported provider or API fails when the agent is constructed instead of
silently dropping the capability.

## Conversations and transcripts

Use `run()` for a single prompt when only the final text matters. Use `converse()` when the
application needs the complete history or a multi-turn conversation:

```python
import os

from agentloop import Agent, Transcript, final_text

transcript = Transcript()

with Agent.from_env(os.environ, tools=[]) as agent:
    transcript.add_user_message("My name is Ada.")
    agent.converse(transcript)

    transcript.add_user_message("What is my name?")
    agent.converse(transcript)

print(final_text(transcript))
print(transcript.model_dump_json(indent=2))
```

A transcript can contain text, model reasoning, local tool calls and results, hosted-tool
activity, sources, and citations. It can be saved as JSON or JSONL and restored later.
Provider-specific data needed to continue a conversation is preserved, while the common
parts remain readable to application code.

For manually assembled or restored conversations, `check_invariants()` verifies tool-call
IDs, result adjacency, completeness, and whether the transcript is safe to send.

## Reliability and errors

Agents built with `Agent.from_env()` receive the configured retry policy automatically.
Rate limits, provider unavailability, timeouts, and invalid responses are retryable;
authentication, bad-request, context-length, missing-model, and credit errors fail
immediately. Server `Retry-After` values are honored within the configured delay limit.

```python
import os

from agentloop import Agent, AgentLoopError
from agentloop.errors import AuthError, RateLimited

try:
    with Agent.from_env(os.environ, tools=[]) as agent:
        print(agent.run("Hello"))
except AuthError:
    print("Check provider credentials")
except RateLimited as error:
    print("The provider remained rate limited", error.retry_after)
except AgentLoopError as error:
    print(f"Agent failed: {type(error).__name__}: {error}")
```

The loop also enforces iteration and wall-clock budgets, protects against truncated tool
calls, and validates that every tool call receives exactly one result.

## Observability

Choose the level of detail that fits the task:

| Need | Use |
|---|---|
| Store how a conversation ended | `ConversationSummary` |
| Debug or audit one conversation step by step | `EventTraceObserver` |
| Follow a conversation across services | OpenTelemetry tracing |
| Build dashboards and alerts | OpenTelemetry metrics |
| Send conversation events to an existing system | A custom observer |

Every conversation produces a `ConversationSummary` on success and failure. It records the
status, model calls, elapsed time, tool calls and errors, retries, token use, and provider-
reported cost. It contains metadata, not prompts, tool arguments, results, or exception
messages.

```python
with Agent.from_env(os.environ, tools=[]) as agent:
    result = agent.run_result(
        "Summarize this account's open support cases.",
        correlation_id="case-review-42",
    )

print(result.text)
print(result.summary.status)
print(result.summary.usage)
```

Read the summary from a result, from `.summary` on an `AgentLoopError`, or in an observer's
`on_conversation_end` callback. Optional `SummaryOptions` add a per-model-call timeline,
warnings as a conversation approaches its limits, and detection of repeated tool calls.

`EventTraceObserver` writes an ordered JSONL event stream to a function supplied by your
application, such as a file writer, logger, or queue. Its default output contains metadata
only. Capturing prompts, responses, tool arguments, results, or error text requires an
explicit setting and a redaction function.

Install the `otel` extra to use OpenTelemetry spans and metrics:

```bash
pip install "based-models-agentloop[otel]"
```

After your application configures OpenTelemetry providers, tracing creates spans for
conversations, model requests, and local tool calls. Metrics cover conversation outcomes and
duration, tool calls and failures, tokens, cost, retries, failed provider attempts (by error
type, phase and whether the request was sent), stalls, and limit warnings. Prompt
and tool content is excluded unless the application explicitly enables capture.

## Testing and custom providers

`agentloop.providers.fake.FakeClient` replays scripted responses and records every request it
receives. It requires no network, provider account, mocking library, or optional SDK, making
agent-loop tests deterministic.

Custom provider integrations implement three members from the `LLMClient` interface:

- `name`
- `model`
- `complete(request) -> ModelResponse`

The interface contains no vendor SDK types, so a custom client works directly with `Agent`
and the rest of the package.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` | `anthropic`, `openai`, `openrouter`, or `modal` |
| `LLM_MAX_ATTEMPTS` | `3` | Total attempts, including the first request |
| `LLM_TIMEOUT_SECONDS` | provider default | Read timeout for one model call (Anthropic: the SDK's overall timeout) |
| `ANTHROPIC_API_KEY` | unset | Anthropic credential |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Anthropic model |
| `OPENAI_API_KEY` | unset | OpenAI credential |
| `OPENAI_MODEL` | `gpt-5.1` | OpenAI model |
| `OPENAI_API` | `responses` | `responses` or `completions` |
| `OPENROUTER_API_KEY` | unset | OpenRouter credential |
| `OPENROUTER_MODEL` | `openrouter/free` | OpenRouter model or router |
| `MODAL_ENDPOINT_URL` | unset | Base URL of a deployed Modal/vLLM endpoint |
| `MODAL_ENDPOINT_MODEL` | `Qwen/Qwen3.6-35B-A3B-FP8` | Model exposed by the endpoint |
| `MODAL_PROXY_TOKEN_ID` | unset | Optional Modal proxy token ID |
| `MODAL_PROXY_TOKEN_SECRET` | unset | Optional Modal proxy token secret |

`Settings` can also be constructed directly when configuration comes from a secrets service
or another application-owned source.

## Documentation

Full documentation coming soon

## Stability and license

`based-models-agentloop` is currently beta software. While the version is `0.x`, a minor
release may change the public API exposed through `agentloop.__all__`.

Licensed under the Apache License 2.0.
