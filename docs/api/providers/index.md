# `agentloop.providers`

Built-in provider integrations convert vendor requests and responses to the shared
`LLMClient` interface. All four support local tools. Hosted web search is available only
with Anthropic Messages and OpenAI Responses.

| Provider | Direct client | API | Hosted web search |
|---|---|---|---|
| [Anthropic](anthropic.md) | `AnthropicClient` | Messages | Yes |
| [OpenAI](openai.md) | `build_openai_client` | Responses by default; Chat Completions optional | Responses only |
| [OpenRouter](openrouter.md) | `build_openrouter_client` | Chat Completions | No |
| [Modal](modal.md) | `build_modal_client` | Chat Completions | No |

Use [`Settings` and `build_client`](../config.md) for provider selection and automatic
retries. Import a direct client from its module when you need provider-specific options.
The `agentloop.providers` package initializer exports no constructors.

Modules whose names start with an underscore, such as `_common` and `_openai_compat`, are
internal implementation details. To add another provider, implement the
[`LLMClient` interface](../provider.md); see the
[custom providers guide](../../guides/custom-providers.md).
