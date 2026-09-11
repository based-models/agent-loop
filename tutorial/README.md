# Build a Provider-Agnostic Coding Agent Loop From Scratch

A hands-on tutorial. You write the code; this explains what to build and why.

The end state is a small, installable, private Python package containing the **core agent loop** of a coding agent harness — the part that turns "here is a task and some tools" into "the model read three files, ran the tests, and fixed the bug." It talks to **Anthropic**, **OpenRouter**, and **Modal** through one seam that the loop itself knows nothing about.

## What you end up with

```
you ──▶ run_conversation(transcript, client, registry)
                │
                │  the loop: ~40 lines, zero vendor knowledge
                │
                ├──▶ client.complete(ModelRequest) ──▶ ModelResponse
                │         │
                │         └── AnthropicClient │ OpenAICompatClient(OpenRouter | Modal)
                │
                └──▶ run_tools(...)  ──▶  read / write / edit / bash
```

Roughly 900 lines of Python across twelve files — pydantic models throughout, with
tests, tracing, and a wheel you can `pip install` from a private git tag.

## What this is not

Not a Claude Code clone. Deliberately **out of scope**: TUI, permission framework, sandboxing, context compaction, subagents, LSP, skills, streaming, async. Chapter 12 shows where each one attaches to what you built — they are all additive, and that is the point of the design.

The one exception is a `before_tool` approval hook, because shipping a `bash` tool with no gate at all is not something you should learn as normal.

## Prerequisites

- **Python 3.12+.** (3.12 for `StrEnum` and the `X | Y` type syntax without quoting;
  3.10 works if you don't mind small edits.)
- **conda** — [Miniforge](https://github.com/conda-forge/miniforge) or Miniconda.
  Chapter 1 sets up an `environment.yml`, and explains why a conda environment and a
  pip-installable package are complementary rather than competing. A plain `venv` works
  just as well if you prefer; the tutorial notes the one-line difference.
- **Intermediate Python.** You're comfortable with classes, functions, dictionaries,
  list comprehensions, exceptions, and running `pytest`. You've seen type hints even if
  you don't write them everywhere.
- Familiarity with what an LLM tool call *is*. You do not need to have written an agent before.

### Python you'll meet along the way

This uses a handful of features that are common in production code but that you may not
have reached for yourself. **Each one is explained in a short "Python note" the first
time it appears** — you don't need to know them going in:

| Feature | Where | What it's for |
|---|---|---|
| pydantic `BaseModel` | ch. 2 | Data classes that check themselves when you build one |
| `ConfigDict(frozen=...)`, `extra="forbid"` | ch. 2 | Read-only models that reject fields you didn't declare |
| `Field(min_length=…, gt=…)` | ch. 2 | Constraints that make illegal values unconstructable |
| Discriminated unions | ch. 2 | Deserializing "one of these four classes" by reading one field |
| `Literal`, `TypeAlias`, `X \| Y` | ch. 2 | Saying precisely what a value is allowed to be |
| `Protocol` | ch. 3 | "Any object with these methods" — the trick the whole seam rests on |
| `BaseSettings`, `SecretStr` | ch. 9 | Config from environment variables, with keys kept out of logs |
| `StrEnum` | ch. 3 | A fixed set of named values that are also strings |
| `TYPE_CHECKING` | ch. 4 | Type hints for an import you don't actually want to load |
| `match` / `case` | ch. 4 | A readable switch statement |
| `inspect.signature().bind()` | ch. 6 | Checking arguments fit a function without calling it |
| `ThreadPoolExecutor` | ch. 6 | Running several tool calls at once |
| Context managers | ch. 7 | `with` blocks that clean up even when something fails |
| `@property` | ch. 8 | A method you read like an attribute |
| `ast` | ch. 11 | Reading a source file's structure without running it |
| `pytest.mark.parametrize` | ch. 11 | One test, many cases |

If a note covers something you already know, skip it — they're set off as blockquotes so
they're easy to scan past.

**Accounts.** You need at least one to finish; you need all three for the chapter 9 payoff.

| Provider | What you need | Notes |
|---|---|---|
| Anthropic | API key | Or `ant auth login` — the SDK reads the profile with no env var set |
| OpenRouter | API key | Free tier is enough; `openrouter/free` routes across free tool-calling models |
| Modal | Account + a deployed vLLM endpoint | Free credits cover it. Chapter 9 has the deploy steps |

## Chapters

| # | File | What you build |
|---|---|---|
| — | [DIVERGENCES.md](DIVERGENCES.md) | Where this departs from the reference course, and why |
| 00 | [00-architecture.md](00-architecture.md) | The shape of the whole thing, before any code |
| 01 | [01-scaffold.md](01-scaffold.md) | The package: uv, extras, `py.typed`, private distribution |
| 02 | [02-transcript.md](02-transcript.md) | Canonical message types + the invariants that keep them legal |
| 03 | [03-provider-seam.md](03-provider-seam.md) | `LLMClient`, `ModelRequest`/`Response`, `StopReason`, the error hierarchy |
| 04 | [04-anthropic-adapter.md](04-anthropic-adapter.md) | The native adapter — the one that isn't OpenAI-shaped |
| 05 | [05-openai-compatible.md](05-openai-compatible.md) | One adapter, two providers, six quirk flags, error classification |
| 06 | [06-tools.md](06-tools.md) | The observer, the registry, and why `run_tools` must be total |
| 07 | [07-loop.md](07-loop.md) | The loop itself, and every guard in it |
| 08 | [08-resilience.md](08-resilience.md) | Retry policy, timeouts, budgets |
| 09 | [09-wire-up-and-run.md](09-wire-up-and-run.md) | `build_client()`, a CLI, and a real run against all three |
| 10 | [10-tracing.md](10-tracing.md) | OpenTelemetry, directly, as an observer |
| 11 | [11-testing.md](11-testing.md) | Fakes, fixtures, golden transcripts, the seam test |
| 12 | [12-next-seams.md](12-next-seams.md) | Where everything deferred attaches |

## How to use this

Each chapter is: **why this exists → what to write → the failure it prevents → a checkpoint you can run.**

The code is shown. How much of it you read before writing your own is up to you — the chapters are written so the prose alone is enough if you want to work that way, and the code blocks are there when you'd rather compare than derive. **The checkpoints are the part you shouldn't skip**: each one is a command with an expected result, and they're what tell you the invariants actually hold rather than merely being described.

Everything shown is meant to be the real implementation, not a simplification you replace
later. Where something is deliberately left for a later chapter it says so; where a
shortcut would be tempting and wrong, it says that too.

Chapters 2 through 8 build in strict dependency order, and nothing runs end-to-end until chapter 9. That's deliberate — you are building a seam, and a seam is only trustworthy if it was designed before anything crossed it.

## Where this comes from

Two sources, and the gap between them is the reason this tutorial exists:

- **[The Coding Agent Loop](https://www.decodingai.com/p/the-coding-agent-loop)** and its [`decode` repository](https://github.com/decodingai-magazine/building-a-coding-agent-from-scratch-course) — the architecture. Its loop is ~20 lines because Pydantic AI already made every hard decision. Those decisions are the subject of this tutorial, so we make them ourselves.
- **Anthropic's tool-use course notebook** — the loop shape: `chat()`, `add_user_message()`, `run_tools()`, `run_conversation()`. Single-provider and Anthropic-native, and two of its habits will break against a current model. We fix both, and say why.

See [DIVERGENCES.md](DIVERGENCES.md) for the full accounting.

Start with [00-architecture.md](00-architecture.md).
