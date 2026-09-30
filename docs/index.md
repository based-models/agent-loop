# agentloop documentation

`agentloop` is a synchronous, provider-agnostic agent loop. It supplies canonical
conversation types, provider adapters, tool execution, retries, observers, and optional
OpenTelemetry tracing while leaving application policy in your code.

## Start here

- [Install agentloop](getting-started/installation.md)
- [Run Hello World with any provider](getting-started/hello-world.md)
- [Configure providers](getting-started/configuration.md)

## Guides

- [Agents](guides/agents.md)
- [Tools and approval](guides/tools.md)
- [Conversations and transcripts](guides/conversations.md)
- [Providers](guides/providers.md)
- [Implement a custom provider](guides/custom-providers.md)
- [Observers](guides/observers.md)
- [Tracing](guides/tracing.md)
- [Installing](guides/installing.md)
- [Releasing](guides/releasing.md)
- [Retries and errors](guides/retries-and-errors.md)
- [Store transcripts](guides/transcript-storage.md)

## Reference and examples

- [API reference](api/index.md)
- [Example index](examples/index.md)

The API reference covers supported names that do not begin with `_`. Underscore-prefixed
modules and members are implementation details and can change without notice.
