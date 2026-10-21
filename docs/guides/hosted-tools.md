# Hosted tools and web search

`HostedTool` is the declaration that `agentloop` passes to a model provider. The provider
runs it; there is no Python handler in your process. OpenAI's Responses API and Anthropic's
Messages API support hosted `web_search`; OpenAI Chat Completions, OpenRouter, and Modal do
not.

The separate `agentloop-tools` library builds these same `HostedTool` objects through
validated helpers such as `native_web_search()`. It also reads sources and citations from
the transcript and offers domain-policy checks. `agentloop` checks support, translates
requests for each provider, and records search activity in the transcript. The example below
uses the core type directly, so it needs only `based-models-agentloop` and the OpenAI extra.

With OpenAI configured as in [Hello World](../getting-started/hello-world.md), declare
web search alongside any local tools:

```python
import os

from agentloop import Agent, HostedTool, HostedToolPart
from agentloop.transcript import TextPart

search = HostedTool(
    kind="web_search",
    options={
        "allowed_domains": ["docs.python.org"],
        "search_context_size": "low",
    },
)

with Agent.from_env(
    os.environ,
    tools=[search],
    system="Search the web before answering. Answer in one sentence.",
) as agent:
    result = agent.run_result("Find the official Python documentation homepage URL.")

print(result.text)
parts = [part for message in result.transcript.messages for part in message.content]
searches = [part for part in parts if isinstance(part, HostedToolPart)]
print(f"search calls: {len(searches)}")
print(f"sources: {sum(len(part.sources) for part in searches)}")
print(f"citations: {sum(len(part.citations) for part in parts if isinstance(part, TextPart))}")
```

Output:

```text
The official Python documentation homepage URL is `https://docs.python.org/3/`.
search calls: 1
sources: 12
citations: 0
```

The provider runs the search and returns its activity in the assistant message. A
`HostedToolPart` records the search query and consulted `Source` entries; citations attached
to `TextPart` connect answer text to a URL. A source shows what the provider consulted, while
a citation shows what the answer cites. Neither contains the searched page's full text.

## Support and options

`Agent` checks hosted-tool support when it is constructed. Asking an unsupported provider or
API to run `web_search` raises `ConfigurationError`. A local `ToolSpec` named `web_search`
also raises `ConfigurationError` when combined with hosted `web_search`; give the local tool
a different name. Hosted searches do not pass through the local tool registry, so
`Deps.before_tool` does not see individual searches.

With `OPENAI_API=completions`, `Agent` rejects hosted search at construction with
`ConfigurationError`. Calling the Chat Completions client directly with hosted tools in a
`ModelRequest` raises `BadRequestError`.

| Option | OpenAI Responses | Anthropic Messages |
|---|---|---|
| `allowed_domains` or `blocked_domains` | Supported | Supported |
| `user_location` | Supported | Supported |
| `search_context_size` | Supported | Logged and ignored |
| `max_uses` | Logged and ignored | Supported |
| `dynamic_filtering` | Logged and ignored | Supported |

Pass either `allowed_domains` or `blocked_domains`; passing both raises
`ConfigurationError`. Unknown options also raise `ConfigurationError`. Anthropic's
`dynamic_filtering=True` requires Claude 4.6+; it changes how search results reach the
model and may leave no citations even when sources are present.

`agentloop` exposes `HostedToolPart`, `Source`, and `Citation`. The companion package's
`native_web_search()` returns a core `HostedTool`; its `sources()`, `citations()`, and
`DomainPolicy` helpers work on core transcripts. It does not run a second search engine.

See the [`HostedTool` reference](../api/provider.md#hostedtool) and
[`transcript` reference](../api/transcript.md#hostedtoolpart).
