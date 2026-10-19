# Hosted tools and web search

A `HostedTool` is a capability the provider runs for you, such as web search. Declare it with
`Agent(tools=[...])` beside your local `ToolSpec`s. `native_web_search(...)` in agentloop-tools
builds one. Native and client-side search are separate, explicit choices: a local `ToolSpec`
named like a hosted kind raises `ConfigurationError`.

```python
from agentloop import Agent, HostedTool

agent = Agent([HostedTool(kind="web_search")], client=client)
```

Support depends on the client (`supported_hosted_tools(client)`). Anthropic and OpenAI over
Responses support `web_search`; Chat Completions providers do not. Approval is per request, not
per search: `before_tool` never sees a hosted call.

**Options.** Unknown options and restriction options a provider cannot honour raise; tuning
options it cannot honour are logged and ignored. OpenAI takes `search_context_size`.

## Sources and citations

The two evidence readers mean different things.

| | `sources(transcript)` | `citations(transcript)` |
|---|---|---|
| What it is | Every page the provider's search returned (`Source(url, title)`), deduplicated | A claim in the model's text tied to a page (`Citation`) |
| Comes from | `HostedToolPart.sources` | `TextPart.citations` |
| Anthropic, basic search (default) | Filled | Filled, with `cited_text` |
| Anthropic, `dynamic_filtering=True` | Filled | **Always empty** |
| OpenAI Responses | Filled | Filled, with `start`/`end` (a span of the answer), no `cited_text` |
| Use it for | "What did it look at?" A reading list, an audit trail | "Where did this sentence come from?" Footnotes, fact-checking |

A source is not evidence that the answer used the page; a citation is. Neither is readable page
text: Anthropic encrypts result content, and OpenAI returns only URLs.

## `dynamic_filtering` (Anthropic only, default `False`)

`True` sends `web_search_20260209`. Claude writes and runs a Python program in a code-execution
sandbox that calls the search, filters the results and prints what it keeps; Claude reads only
that output. `False` sends `web_search_20250305` and results go straight into the model's
context.

- **Citations disappear** because a citation must point at a search-result block in the model's
  context, and with filtering the results go to the program. Measured live in September 2026
  (`claude-opus-5`, 2 questions x 3 variants): filtering on gave 0 citations every time, basic
  search 2-3, and `web_search_20260209` with `allowed_callers: ["direct"]` 2-5. The cause is the
  code path, not the tool version. Frozen captures are in
  `agentloop-tools/experiments/examples/citations/` (`shannon_A_dynamic.json` beside
  `shannon_B_basic.json`); `experiments/` is gitignored, so rerun
  `experiments/citations_experiment.py` on a fresh clone (6 requests, a few cents).
- **Cost:** it did not save tokens on simple searches (13,300 vs 12,827 input tokens). Use it for
  search-heavy requests that do not need citations.
- **Constraints:** Claude 4.6+ only; not ZDR-eligible; the response carries extra
  `server_tool_use` / `code_execution_tool_result` blocks, surfaced as a `HostedToolPart` of
  kind `code_execution`.
- **OpenAI** has no equivalent: `dynamic_filtering=True` logs a warning and is ignored.

## Domain policy (`agentloop_tools.web.DomainPolicy`)

It prevents and reports, and does nothing else.

- **Modes:** `DomainPolicy.block([...])` or `DomainPolicy.allow([...])`, not both.
- **Matching:** a rule covers its host and every subdomain, not a longer label (`europa.eu`
  covers `eur-lex.europa.eu`, not `noteuropa.eu`). A rule may add a path prefix where `*`
  matches within the path.
- **Prevent:** `policy.native_web_search(**options)` sends the list to the provider; passing a
  domain option there as well is an error.
- **Report:** `policy.audit(transcript, since=n)` returns `Violation(url, where, message_index,
  rule)`, with `where` of `"source"`, `"citation"` or `"text"`. To audit one turn, pass the
  `len(transcript.messages)` taken before `converse`.
- **Text scanning:** full URLs in both modes, balanced parentheses kept; bare host mentions in
  block mode only.
- **The hard limit:** with native search no library can stop the model reading a page. The
  policy prevents what the provider can and reports the rest. Only client-side search filters
  before the model sees anything.
- **Live evidence (September 2026):** `block(["wikipedia.org"])` held on both providers (0 leaked
  sources out of 10 and 31). Asked for Wikipedia's URL, Claude wrote 4 Wikipedia links from
  memory, which the audit reported as `"text"` violations. `allow(["python.org"])` on OpenAI
  returned 27 sources, all on the list.
