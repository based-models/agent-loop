# agentloop documentation

`agentloop` is a synchronous Python agent loop for Anthropic, OpenAI, OpenRouter, Modal/vLLM,
and custom clients. It supplies shared conversation types, tool execution, retries, limits,
and operational reporting. Your application chooses prompts, tools, approvals, and secrets.

## Start here

1. [Install agentloop](getting-started/installation.md).
2. [Run Hello World](getting-started/hello-world.md) with the included `FakeClient` to see
   the loop work offline, then use the same `Agent.run()` call with a live provider.
3. [Configure a provider](getting-started/configuration.md) with an explicit mapping or
   `Settings` object.

## Guides

- [Agents](guides/agents.md)
- [Tools and approval](guides/tools.md)
- [Conversations and transcripts](guides/conversations.md)
- [Providers](guides/providers.md)
- [Hosted tools and web search](guides/hosted-tools.md)
- [Implement a custom provider](guides/custom-providers.md)
- [Conversation summaries](guides/summaries.md)
- [Event traces](guides/event-trace.md)
- [Observers](guides/observers.md)
- [OpenTelemetry tracing and metrics](guides/tracing.md)
- [Installing](guides/installing.md)
- [Retries and errors](guides/retries-and-errors.md)
- [Store transcripts](guides/transcript-storage.md)

## Reference and examples

- [API reference](api/index.md)
- [Example index](examples/index.md)

The API reference describes the main public interfaces. Names beginning with `_` are
internal and can change without notice.
