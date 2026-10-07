# `agentloop.providers`

Built-in provider integrations convert vendor APIs to the shared `LLMClient` interface. The
package initializer exports nothing; import constructors from their modules.

- [Anthropic](anthropic.md)
- [OpenAI](openai.md)
- [OpenRouter](openrouter.md)
- [Modal](modal.md)

The modules `_common` and `_openai_compat` are private implementation details despite being
importable in Python. Applications should use `Settings`/`build_client`, the concrete public
constructors, or your own `LLMClient` implementation.
