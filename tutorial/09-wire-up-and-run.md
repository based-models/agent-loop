# 09 — Wire it up and run it

Seven chapters of contract. This is where it runs.

Builds `src/harness/config.py` and `src/harness/cli.py`.

---

## `Settings` — explicit, not a singleton

Divergence **D13**. The reference course does:

```python
# decode/config/settings.py
settings = Settings()                    # module level — runs at import
```

and `from decode.config.settings import settings` at twenty call sites. Correct for an application. Wrong for a library, for the reasons in chapter 1: importing your package would read a file off disk, import order becomes load-bearing, tests need monkeypatching, and a consumer whose config lives somewhere else finds yours already won.

```python
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider: TypeAlias = Literal["anthropic", "openrouter", "modal"]


class Settings(BaseSettings):
    """Configuration. Construct it explicitly; `from_env()` is opt-in."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        frozen=True,
        protected_namespaces=(),        # we have fields called *_model
        # NOTE: no `env_file=`. Reading .env is the CLI's job, not the library's.
    )

    provider: Provider = Field(default="anthropic", validation_alias="LLM_PROVIDER")

    anthropic_api_key: SecretStr | None = None    # None => let the SDK resolve it
    anthropic_model: str = "claude-opus-5"

    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"

    modal_endpoint_url: str = ""
    modal_endpoint_model: str = "Qwen/Qwen3.6-35B-A3B-FP8"
    modal_proxy_token_id: SecretStr | None = None
    modal_proxy_token_secret: SecretStr | None = None

    max_retries: int = Field(default=3, ge=1)

    @field_validator("anthropic_api_key", "openrouter_api_key",
                     "modal_proxy_token_id", "modal_proxy_token_secret", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """An empty string means "not configured", not "configured as empty"."""
        return None if value == "" else value

    @classmethod
    def from_env(cls) -> Settings:
        """Opt-in. Called by the CLI, never by the library."""
        return cls()        # BaseSettings reads os.environ itself
```

Four details, and the first is the one that changes most from a plain class.

**`BaseSettings` reads the environment for you.** Each field is populated from the
matching upper-cased environment variable — `anthropic_model` from `ANTHROPIC_MODEL` —
with the declared default as the fallback. `provider` takes an explicit
`validation_alias` because the variable is `LLM_PROVIDER`, not `PROVIDER`.

**Validation is now free and better.** `LLM_PROVIDER=gemini` fails at construction with
`Input should be 'anthropic', 'openrouter' or 'modal'` — naming the field, the bad value,
and the valid ones. The hand-written `if provider not in (...)` check is gone.

**`SecretStr` keeps credentials out of logs.** Its `repr` is `SecretStr('**********')`,
so a stray `print(settings)` or an exception that includes the settings object cannot
leak a key. Read the real value with `.get_secret_value()`, deliberately, at the one
place you need it.

**An empty string is not the same as unset.** `ANTHROPIC_API_KEY=` in a half-filled
`.env` would otherwise give you `SecretStr("")`, which breaks the SDK's credential chain;
`None` lets it fall through to `ANTHROPIC_AUTH_TOKEN` and then to an `ant auth login`
profile. The `_blank_is_unset` validator normalizes that once, for all four secrets,
instead of at every call site.

> **Python note.** `mode="before"` runs the validator on the *raw* input, before pydantic
> tries to build a `SecretStr` from it — which is what you want, since you're deciding
> whether there is a value at all. `@classmethod` under `@field_validator` is required by
> pydantic; the order of the two decorators matters.

### Where `.env` is read

```python
# cli.py
from dotenv import load_dotenv
```

`load_dotenv()` populates `os.environ` from a `.env` file in the current directory, if
one exists, and does nothing if it doesn't. It never overwrites a variable that is
already set, so a real environment variable always beats the file — which is what you
want on a server.

It lives in `cli.py` and in the `[cli]` extra, not in `Settings`. Note also that
`SettingsConfigDict` above deliberately has **no** `env_file=`: pydantic-settings would
happily read the file for you, and that is exactly the behaviour a library must not have.

### Still not a singleton

`Settings` is a `BaseSettings`, which is exactly what the reference course uses — but
divergence **D13** was never about pydantic. It was about this:

```python
settings = Settings()          # module level, at import. Don't.
```

There is no module-level instance here. `from_env()` is a classmethod the CLI calls once,
and `build_client(settings)` takes the result as an argument. Note also the absence of
`env_file=".env"` in the config: reading that file is the CLI's job (below), because a
library that reads the filesystem on import is a library that surprises everyone who
installs it.

---

## `build_client()` — returns a client, not a model

Divergence **D5**.

```python
def build_client(settings: Settings, *,
                 observer: LoopObserver | None = None) -> LLMClient:
    """Construct the configured provider's client, ready to use."""
    match settings.provider:
        case "anthropic":
            # Not wrapped in RetryingClient: the SDK already retries twice (chapter 8).
            return _build_anthropic(settings)
        case "openrouter":
            inner = _build_openrouter(settings)
        case "modal":
            inner = _build_modal(settings)
        case _:
            raise ValueError(f"unsupported provider: {settings.provider!r}")

    return RetryingClient(
        inner,
        policy=RetryPolicy(max_attempts=settings.max_retries),
        observer=observer,
    )


def _build_anthropic(settings: Settings) -> LLMClient:
    from harness.providers.anthropic import AnthropicClient      # lazy — see below

    key = settings.anthropic_api_key
    return AnthropicClient(
        api_key=key.get_secret_value() if key else None,   # None => SDK resolves it
        model=settings.anthropic_model,
    )


def _build_openrouter(settings: Settings) -> LLMClient:
    from harness.providers.openrouter import build_openrouter_client

    if settings.openrouter_api_key is None:
        raise AuthError("OPENROUTER_API_KEY is not set")
    return build_openrouter_client(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.openrouter_model,
    )


def _build_modal(settings: Settings) -> LLMClient:
    from harness.providers.modal import build_modal_client

    if not settings.modal_endpoint_url:
        raise ValueError("MODAL_ENDPOINT_URL is not set")
    token_id, token_secret = settings.modal_proxy_token_id, settings.modal_proxy_token_secret
    if (token_id is None) != (token_secret is None):
        raise ValueError(
            "MODAL_PROXY_TOKEN_ID and MODAL_PROXY_TOKEN_SECRET must be set together "
            "(or both left unset for an --unauthenticated endpoint)"
        )
    return build_modal_client(
        endpoint_url=settings.modal_endpoint_url,
        model=settings.modal_endpoint_model,
        proxy_token_id=token_id.get_secret_value() if token_id else "",
        proxy_token_secret=token_secret.get_secret_value() if token_secret else "",
    )
```

> **Python note.** `match` / `case` is Python 3.10's version of a switch statement.
> `case "anthropic":` runs when `settings.provider` equals that string, and `case _:`
> is the catch-all. It reads better than a chain of `elif`s here because there is one
> value being dispatched on.

One function per provider rather than three bodies inside the `match`. Each one is
small, independently testable, and — the reason that matters — has its own scope for
the lazy import below.

### Why a *client* and not a *model*

The reference's `_build_model()` returns a Pydantic AI `Model`, which the `Agent` then drives. So something above the factory still has to know how to drive that model — provider knowledge leaks upward into the loop's neighborhood.

Returning a fully-configured client satisfying a one-method protocol means the loop has literally zero provider knowledge, and every test injects a fake at exactly one point. Retry policy, timeouts, quirks, auth: all baked in below the line.

### Every import is inside a branch

```python
case "anthropic":
    from harness.providers.anthropic import AnthropicClient
```

Not at module top. This is what makes chapter 1's optional extras real: a Modal-only install has no `anthropic` package, and a top-level import here would crash `import harness.config` before anyone chose a provider. Chapter 11's wheel test verifies it.

### Both-or-neither, checked at startup

```python
if (token_id is None) != (token_secret is None):
    raise ValueError(...)
```

> **Python note.** Comparing two boolean expressions with `!=` asks "is exactly one of
> these true?" — which is the condition to reject. The `_blank_is_unset` validator on
> `Settings` is what makes `is None` the right test: an empty environment variable has
> already been normalized to `None` by the time this runs.

One of two set is always a misconfiguration. Without this you get a 401 with a plain-text body on your first request — the exact failure chapter 8's `_extract_message` handles, but there's no reason to make it handle a case you can catch at startup.

---

## The CLI

The only place in the codebase that reads the environment.

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level,
                        format="%(levelname)s %(name)s: %(message)s")

    # Reading .env is the APPLICATION's job. `Settings` deliberately does not do it.
    load_dotenv()                               # python-dotenv, from the [cli] extra
    settings = Settings.from_env()              # <- the one call
    observer = _build_observer(args)            # chapter 10 swaps this
    client = build_client(settings, observer=observer)

    deps = Deps(cwd=Path(args.cwd).resolve(), before_tool=deny_destructive)
    registry = Registry([READ, WRITE, EDIT, BASH])
    transcript = Transcript()

    print(f"harness · {client.name} · {client.model} · cwd={deps.cwd}")
    while True:
        try:
            prompt = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not prompt or prompt in {"exit", "quit"}:
            return 0

        transcript.add_user_text(prompt)
        try:
            run_conversation(transcript, client, registry, deps,
                             system=SYSTEM_PROMPT, observer=observer)
        except ContextLengthExceeded:
            print("\n[conversation is too long — start a new session]")
            return 1
        except HarnessError as e:
            print(f"\n[{type(e).__name__}] {e}")
            continue

        print(_final_text(transcript))
```

Note it catches `ContextLengthExceeded` separately and **exits**. Chapter 8's rule: compaction is out of scope, so fail loudly rather than spin. Other `HarnessError`s keep the REPL alive.

### The observer is the UI

```python
class ConsoleObserver(NullObserver):
    def conversation(self): return nullcontext()

    def on_request(self, request):
        logger.debug("→ %d messages, %d tools", len(request.messages), len(request.tools))

    def on_response(self, response):
        for part in response.content:
            if isinstance(part, TextPart) and part.text.strip():
                print(part.text)
        u = response.usage
        logger.debug("← %s  in=%d out=%d", response.stop_reason,
                     u.input_tokens, u.output_tokens)

    def on_tool_start(self, call):
        print(f"  ⚙ {call.name}({_brief(call.arguments)})")

    def on_tool_end(self, call, result):
        mark = "✗" if result.is_error else "✓"
        print(f"  {mark} {_first_line(result.content)}")

    def on_retry(self, attempt, error):
        print(f"  ↻ retry {attempt}: {error}")
```

This is deliberately the **same `LoopObserver`** a TUI would implement. Chapter 7 shaped the protocol as enter/exit pairs for exactly this reason — swapping `ConsoleObserver` for a prompt_toolkit app touches nothing in `loop.py`, and chapter 10 swaps in an OTel observer the same way.

### The system prompt

```python
SYSTEM_PROMPT = """\
You are a coding agent working in a developer's project directory.

- Read before you edit. Never guess a file's contents.
- Prefer `edit` over `write` for existing files; `write` replaces the whole file.
- After changing code, run the project's tests or linter with `bash` if you can find them.
- Report what you actually did. Never claim output you have not seen.
- If you lack a tool for something, say so plainly rather than pretending."""

```

Small, and load-bearing. "Read before you edit" and "never claim output you have not seen" are the two that measurably change behavior — the first prevents `edit` calls against imagined content, the second prevents confident fabricated test results.

---

## Run it

### Anthropic

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...        # or: ant auth login
harness --cwd /path/to/a/scratch/project
```

```
harness · anthropic · claude-opus-5 · cwd=/tmp/scratch

> what does main.py do?
  ⚙ read({"path": "main.py"})
  ✓      1	def main():
It reads a CSV and prints row counts per column.
```

Start in a **scratch directory**, not a real project. The agent has `write`, `edit`, and `bash`, and the `before_tool` hook is a guardrail, not a boundary.

### OpenRouter

```bash
export LLM_PROVIDER=openrouter
export OPENROUTER_API_KEY=sk-or-...
harness --cwd /tmp/scratch
```

`openrouter/free` spreads across free models and auto-filters for tool-calling support, so one congested upstream can't hard-block you. Pin a `:free` model id for a stricter guarantee about what you're actually talking to.

### Modal

Deploy an OpenAI-compatible vLLM endpoint:

```bash
pip install modal && modal setup
modal deploy vllm_inference.py          # from modal.com/docs/examples/vllm_inference
```

Adapt the example to a tool-calling model and start vLLM with a matching parser:

```
--enable-auto-tool-choice --tool-call-parser hermes
```

**Without `--tool-call-parser`, vLLM will not emit `tool_calls` at all** — the model writes its call into plain text and your agent does nothing, with no error anywhere. This is the single most common Modal setup failure. The parser name must match the model family (`hermes`, `llama3_json`, `mistral`, …).

```bash
export LLM_PROVIDER=modal
export MODAL_ENDPOINT_URL=https://you--vllm-serve.modal.run
export MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
# authenticated endpoints only — both or neither:
export MODAL_PROXY_TOKEN_ID=wk-...
export MODAL_PROXY_TOKEN_SECRET=ws-...
harness --cwd /tmp/scratch
```

**The first request will take minutes.** Cold start spins a container and loads weights. That's what `connect=60.0, read=900.0` is for. If it fails with a timeout, run it again — the container is warm now.

---

## Checkpoint: the same task, three providers

The real proof, and the reason the seam was built before anything crossed it.

```bash
mkdir -p /tmp/harness-check && cd /tmp/harness-check
cat > buggy.py <<'EOF'
def average(values):
    return sum(values) / len(values)

if __name__ == "__main__":
    print(average([]))
EOF

for p in anthropic openrouter modal; do
  LLM_PROVIDER=$p harness --cwd /tmp/harness-check \
    --once "buggy.py crashes. Read it, fix it, and run it to confirm." \
    --dump-transcript "/tmp/transcript-$p.jsonl"
done
```

(`--once` runs a single prompt non-interactively; `--dump-transcript` writes one
`message.model_dump_json()` per line, which chapter 2 gave you for free.)

Then compare **structure**, not content:

```bash
for p in anthropic openrouter modal; do
  echo -n "$p: "
  python3 -c "
import json,sys
msgs=[json.loads(l) for l in open(sys.argv[1])]
print(' '.join(
  m['role'][0] + ('T' if any(p['type']=='tool_call' for p in m['content']) else
                  'R' if any(p['type']=='tool_result' for p in m['content']) else 't')
  for m in msgs))
" "/tmp/transcript-$p.jsonl"
done
```

```
anthropic:  ut aT uR aT uR aT uR at
openrouter: ut aT uR aT uR at
modal:      ut aT uR aT uR aT uR at
```

**What you are looking for:** the same *alphabet*. `u`ser/`a`ssistant, `t`ext / tool `T`all / tool `R`esult. Every transcript alternates correctly, every `aT` is followed by a `uR`, every run terminates on `at`.

**What differs, and should:** the number of turns. Different models solve it in different numbers of steps. Opus might read once and fix; a smaller self-hosted model might read, fix, run, read the error, fix again.

That's the whole claim, demonstrated: **the differences are in the model's behavior, not in the shape of the conversation.** If one provider produced a `uR` without a preceding `aT`, or terminated on something other than `at`, the seam leaks.

Run `Transcript.check_invariants()` over each dump as a harder version of the same check:

```bash
python -c "
from harness.transcript import load_jsonl
for p in ('anthropic','openrouter','modal'):
    load_jsonl(f'/tmp/transcript-{p}.jsonl').check_invariants()
    print(f'{p}: ok')
"
```

---

## If something went wrong

| Symptom | Cause |
|---|---|
| `400 ... temperature` | You sent `temperature` to a current Claude model. D9 — omit it when `None` |
| `400 ... max_tokens` | Anthropic requires it. Chapter 3 |
| `400 tool_use ids were found without tool_result` | Something skipped a result. Chapter 6's totality broke |
| Agent answers without ever calling a tool (Modal) | Missing `--tool-call-parser`. vLLM isn't extracting calls |
| Agent answers without ever calling a tool (any) | Check `raw_stop_reason` — if it's `"stop"` with populated `tool_calls`, Rule 1 isn't firing |
| `JSONDecodeError` inside your error handler | `_extract_message` assumed JSON. Modal's 401 is plain text |
| Hangs, then `ReadTimeout` (Modal) | Cold start. Run it again |
| `ModuleNotFoundError: anthropic` | A top-level import crept into `providers/anthropic.py` or `config.py` |
| `400` template render error (Modal) | Two system messages, or one not first. Shouldn't be reachable — check `_to_wire` |

---

Next: [10-tracing.md](10-tracing.md) — now that it runs, watch it.

**Sources.** [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/) (`--enable-auto-tool-choice`, `--tool-call-parser` and the per-model parser names) · [Modal vLLM inference example](https://modal.com/docs/examples/vllm_inference) · [Modal proxy auth](https://modal.com/docs/guide/webhook-proxy-auth) · [OpenRouter models](https://openrouter.ai/docs/guides/features/tool-calling). Anthropic SDK credential resolution is from the bundled `claude-api` skill reference.
