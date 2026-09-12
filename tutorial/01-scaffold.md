# 01 — Scaffold

You are building a **library**, not a script. That decision changes things this early — how config works, which imports are allowed where, whether `.env` is ever read — so it goes first rather than being bolted on at the end.

---

## Two files, two jobs

Before any commands, the distinction that makes the rest of this chapter make sense —
and answers the obvious question, *can I use conda and still ship a pip-installable
package?* Yes, because they are not competing:

| File | Answers | Who reads it |
|---|---|---|
| `environment.yml` | "What do **I** need on this machine to work on this?" | You, and other contributors |
| `pyproject.toml` | "What does this **package** need, wherever it's installed?" | pip, and everyone who installs your wheel |

A conda environment gives you a Python interpreter and any non-Python system libraries.
Your package's own dependencies are declared in `pyproject.toml` and installed into that
environment by pip. Someone who later runs `pip install harness` gets the wheel and the
dependencies from `pyproject.toml`; they never see your `environment.yml`, and that is
correct — it is a development convenience, not part of the distribution.

**The rule that keeps them from drifting: never list the same dependency in both.**
`environment.yml` names Python and pip, and then points at the project. That is it.

---

## Create the package

```bash
mkdir -p based-models-coding-harness/src/harness/providers
cd based-models-coding-harness
git init
```

The **`src/` layout** matters more than it looks. Without it, `import harness` picks up
the copy in your working directory, and your tests never exercise the *installed*
package — which means the packaging bug in chapter 11 (an adapter importing its SDK
eagerly) stays invisible until a consumer hits it.

---

## `pyproject.toml`

This is the source of truth for everything about the package.

```toml
[project]
name = "harness"
version = "0.1.0"
description = "A provider-agnostic coding agent loop."
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "opentelemetry-api>=1.30",
]

[project.optional-dependencies]
anthropic = ["anthropic>=1.0"]
openai    = ["httpx>=0.27"]
otel      = ["opentelemetry-sdk>=1.30", "opentelemetry-exporter-otlp-proto-http>=1.30"]
cli       = ["pydantic-settings>=2.3", "python-dotenv>=1.0"]
all       = ["harness[anthropic,openai,otel,cli]"]
dev       = ["pytest>=8", "mypy>=1.11", "ruff>=0.6", "build>=1.2"]

[project.scripts]
harness = "harness.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
strict = true
files = ["src", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Four decisions worth understanding rather than copying.

### `requires-python = ">=3.12"`

For `StrEnum` and unquoted `X | Y` annotations in runtime positions. On 3.10/3.11 you'd
use `class StopReason(str, Enum)` and keep `from __future__ import annotations`
everywhere. Not hard, just noise.

Note this is a *constraint on consumers*, not a statement about your machine — that's
what `environment.yml` pins.

> **On the `anthropic>=1.0` pin.** The Anthropic Python SDK went to 1.x, which brought
> breaking changes (httpx 2, awaited async `.with_raw_response`, removed deprecated
> parameters). Chapter 4's code is written against 1.x. If you find a snippet elsewhere
> that predates it, check it against the current SDK before copying.

### Every provider SDK is an *optional* extra

```bash
pip install "harness[anthropic]"     # Anthropic only — no httpx
pip install "harness[openai]"        # OpenRouter/Modal only — no anthropic
pip install "harness[all]"
```

Someone running only against their own Modal endpoint should not have the Anthropic SDK
on disk. This holds only if two things are true:

1. **Core never imports a provider SDK.** Enforced by test in chapter 11.
2. **Each adapter imports its SDK lazily**, inside the function that needs it, with an
   error naming the missing extra:

```python
def build_anthropic_client(settings: Settings) -> LLMClient:
    try:
        import anthropic
    except ImportError as e:
        raise ImportError(
            "The Anthropic provider needs the SDK. "
            "Install: pip install 'harness[anthropic]'"
        ) from e
    ...
```

A top-level import in `providers/anthropic.py` is enough to break a Modal-only install,
because `config.py` imports that module to reach the factory. Chapter 4 covers placement.

> **On the ruff rule set.** `E`/`F` are the pycodestyle and pyflakes basics, `I` sorts
> imports, `UP` modernizes syntax for your `requires-python`, `B` catches likely bugs,
> `SIM` simplifies. Deliberately **not** enabled: `TCH`, which moves annotation-only
> imports into `if TYPE_CHECKING:` blocks. It is a real import-time optimization and it
> is pure ceremony in a codebase this size — chapter 4 introduces `TYPE_CHECKING` where
> it genuinely earns its place.

### `dev` is an extra, not a dependency group

You may have seen `[dependency-groups]` used for development tools. It is a newer
standard, and support outside the tool that popularized it is uneven — so for a
conda-plus-pip workflow, a plain `dev` extra is the portable choice. `pip install
-e ".[dev]"` works everywhere, today.

### Two hard dependencies, and why each earns it

**`pydantic`** models every piece of data that crosses a boundary — the transcript
(chapter 2), the request and response types (chapter 3). It buys four things at once:
constrained types so illegal values can't be constructed, one canonical shape instead of
`str | list` branching, JSON round-tripping with no loader of your own, and validation
errors that name the field. Chapter 2 makes that case properly.

It is *not* used to generate tool schemas — those stay hand-written JSON Schema dicts
(divergence D7), for reasons chapter 6 explains.

**`opentelemetry-api`** is not a contradiction of "minimal dependencies." The OTel
**API** package is a no-op when no SDK is configured — it hands out non-recording spans, allocates almost nothing, and
makes no network calls. A consumer who doesn't want tracing pays essentially zero.

The **SDK** and exporter — the parts that allocate, batch, and send — are in the `otel`
extra. Chapter 10.

---

## `environment.yml`

```yaml
name: harness
channels:
  - conda-forge
dependencies:
  - python=3.12
  - pip>=24.0
  - pip:
      # Everything else comes from pyproject.toml. Editable, so your source
      # changes take effect without reinstalling.
      - -e .[all,dev]
```

That is the whole file, and the `-e .[all,dev]` line is why. It tells pip to install
*this project* in editable mode with the `all` and `dev` extras — so conda provides the
interpreter, and `pyproject.toml` provides every actual dependency. Add `httpx` to
`pyproject.toml` and this file does not change.

The temptation is to list `anthropic`, `httpx`, and `pytest` under conda's
`dependencies:` instead. Resist it: you would then have two lists that must agree, and
the day they disagree you get a bug that only reproduces on someone else's machine.

Create and activate:

```bash
conda env create -f environment.yml
conda activate harness
```

Run `conda env create` **from the project root** — the `-e .` is relative to your working
directory, not to the file.

> **If `conda` misbehaves in a script.** `conda` is a *shell function*, installed by
> `conda init` into your `.zshrc`/`.bashrc`. Non-interactive shells often don't load
> that, and the symptom is nasty: the function fails to resolve its own helper, prints
> something like `__conda_exe:6: permission denied`, and **exits 0** — so a script
> carries on as if the environment were created. If you hit that, call the binary
> directly:
>
> ```bash
> "$(conda info --base)/bin/conda" env create -f environment.yml
> ```
>
> Or on macOS with Homebrew's miniconda, typically
> `/opt/homebrew/Caskroom/miniconda/base/bin/conda`. Always check the exit status *and*
> that the environment appears in `conda env list`.

When dependencies change in `pyproject.toml`:

```bash
conda env update -f environment.yml --prune
```

`--prune` removes packages that are no longer required. Without it, a dependency you
deleted stays installed, your imports keep working locally, and the failure surfaces for
someone else.

> **Note on `conda env export`.** It produces a full, platform-specific list of every
> resolved package — useful as a lock file for reproducing a bug, wrong as your
> `environment.yml`. If you want both, commit the hand-written one as `environment.yml`
> and an exported one as `environment.lock.yml`, and be clear about which is which.

### If you'd rather not use conda

Nothing in this tutorial depends on it. A plain virtual environment is:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
```

Same result. `pyproject.toml` is doing the work either way — which is the point of the
split at the top of this chapter.

---

## The rest of the skeleton

```bash
mkdir -p tests
touch src/harness/__init__.py src/harness/py.typed
touch src/harness/providers/__init__.py
```

### `py.typed`

An empty file whose presence is the signal. By convention — [PEP 561](https://peps.python.org/pep-0561/) —
a type checker ignores a package's type hints unless it finds this marker. Without it,
someone who installs your package and runs mypy sees `harness` as completely untyped:
every `ModelResponse` is just `Any`, and none of your annotations help them. You're
writing all this code under `mypy --strict`; the marker is what lets anyone else benefit
from that.

Hatchling includes it automatically with the src layout. Chapter 11 verifies it by unzipping the built wheel — worth doing, because a missing marker fails silently.

### `.env.example`

```bash
# Read by the CLI only. The library never reads this file.

# One of: anthropic | openrouter | modal
LLM_PROVIDER=anthropic

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-opus-5

OPENROUTER_API_KEY=
OPENROUTER_MODEL=openrouter/free

MODAL_ENDPOINT_URL=
MODAL_ENDPOINT_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
MODAL_PROXY_TOKEN_ID=
MODAL_PROXY_TOKEN_SECRET=

# Optional: tracing (chapter 10). The application configures OTel, not the library.
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

Add a `.gitignore` while you're here:

```gitignore
.env
__pycache__/
*.egg-info/
dist/
.mypy_cache/
.pytest_cache/
.ruff_cache/
# If you generate a lock file (see the note above), ignore it too:
environment.lock.yml
```

> **Gotcha.** `.gitignore` only treats `#` as a comment at the **start** of a line. A
> trailing `pattern   # note` makes the note part of the pattern, and the rule silently
> never matches anything.

---

## The one rule that shapes everything else

> **The library never reads the environment. The CLI reads it once, explicitly, and passes the result down.**

This is divergence **D13**, and it is the single most common way a good library becomes annoying to depend on.

The reference course does this — **their code, quoted, not yours.** (Note the providers:
`decode` ships Gemini where this tutorial ships Anthropic, which is divergence D3. Ignore
the provider list; the problem is the last line.)

```python
# decode/config/settings.py  — the REFERENCE course, for contrast
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    llm_provider: Literal["gemini", "openrouter", "modal"] = "gemini"
    ...

settings = Settings()        # <-- module level: constructed at import time
```

and then `from decode.config.settings import settings` at twenty call sites.

Chapter 9 builds our version: `Literal["anthropic", "openrouter", "modal"]`, and no
module-level instance.

That is **correct for an application**, and `decode` is an application. For a library it means:

- Importing your package reads a file off disk and mutates process-visible state. Import order becomes load-bearing.
- Tests need monkeypatching to control behavior, because the object was built before the test ran.
- A consumer whose config lives in Vault, or in their Django settings, or in a Kubernetes ConfigMap, finds that yours already ran and lost.
- Two different configurations in one process — a cheap model for subagents, an expensive one for the main loop — are impossible.

So instead: `Settings` is a plain frozen dataclass you construct. `Settings.from_env()` exists, is opt-in, and is called in exactly one place — `cli.py`. Everything below takes it as an argument. Chapter 9 writes it.

The same rule applies to tracing in chapter 10: the library never calls `set_tracer_provider()` and never reads `OTEL_*`. The application owns global state; the library asks for what it needs.

---

## Private distribution

The code stays private. Two stages; do the first now, the second when you need it.

### Now: git tags over SSH

```bash
git add -A && git commit -m "scaffold"
git tag v0.1.0 && git push --tags
```

Consumers:

```bash
pip install "harness[anthropic] @ git+ssh://git@github.com/you/based-models-coding-harness@v0.1.0"
```

Or, in their own `environment.yml`:

```yaml
  - pip:
      - harness[anthropic] @ git+ssh://git@github.com/you/based-models-coding-harness@v0.1.0
```

Zero infrastructure, and auth is the SSH key the developer already has. **Pin a tag, not
a branch** — `@main` means a rebuild six weeks from now silently pulls different code.

### Later: a private index

Once there is more than one consumer, or you want CI to install without SSH keys, publish wheels to GitHub Packages, AWS CodeArtifact, GCP Artifact Registry, or Gemfury:

```bash
pip install --index-url https://your-index/simple/ "harness[anthropic]"
```

Sketched in chapter 12. Not worth the setup yet.

---

## Checkpoint

With the environment activated:

```bash
pytest
ruff check .
mypy
python -m build
```

Expected:

- `pytest` — `no tests ran`. That is a pass; there are no tests yet.
- `ruff` — `All checks passed!`
- `mypy` — `Success: no issues found`
- `python -m build` — writes `dist/harness-0.1.0-py3-none-any.whl` and a `.tar.gz`

Then confirm the package is importable *as installed*, not just as files on disk:

```bash
python -c "import harness; print(harness.__file__)"
```

It should print a path inside `src/harness/` — that is what editable mode means. If it
errors, the `pip install -e` step didn't run; `conda env update -f environment.yml` will
fix it.

Finally, confirm the typing marker actually shipped:

```bash
unzip -l dist/harness-0.1.0-py3-none-any.whl | grep py.typed
```

If that line is missing, consumers get no types from you. Check that
`src/harness/py.typed` exists and sits *inside* the package directory, not beside it.

---

Next: [02-transcript.md](02-transcript.md) — the canonical message types, and the invariants that keep them legal.
