# CLAUDE.md

`agentloop` (PyPI: `based-models-agentloop`) is a provider-agnostic agent loop library:
reason, act, observe, repeat, with tools, retries and observability. Python 3.12+, pydantic v2.

## Commands

Environment: conda env `agentloop` (see `environment.yml`; editable install with `[all,dev]`).

```bash
bash scripts/check.sh              # ruff + mypy + pytest with the 88% coverage gate + bare-import check
bash scripts/check.sh --no-cov     # the same without coverage, for quick loops
conda run -n agentloop python -m pytest -q tests/test_summary.py   # one file
```

`PYTHON=<interpreter>` selects the Python the scripts use (default `python`).

**In a Claude Code session the sandbox shell cannot run these.** Its `python3` is 3.9 (this
project needs 3.12) and `conda` is not usable from it. Run them through the Terminal panel
(`run_in_terminal`) as `conda run -n agentloop ...`, redirect the output to a file in the
scratchpad, and read it back. Close terminal tabs when done; there is a cap on open tabs.

CI (`.github/workflows/ci.yml`) and the release workflow run the same checks inline; keep
`scripts/check.sh` in step with them.

## Architecture

The seam: **the loop emits facts only it can know; everything else is an observer.**

- `loop.py`: iterations, limits, elapsed time, stop status, ids. No reporting policy.
- `observer.py`: the `LoopObserver` protocol. New hooks must be **optional** (`NullObserver`
  no-op, fan-out in `MultiObserver`, skipped by `GuardedObserver` when absent).
- Observers that aggregate or export: `collector.py` (`SummaryCollector`), `events.py`
  (JSONL event trace), `metrics.py` and `tracing.py` (OpenTelemetry), `summary.py` (the models).
- `provider.py` is the canonical request/response seam; vendor code lives only in `providers/`.
- `retry.py` stamps `attempts` / `retry_wait_seconds` onto responses and errors so retries are
  counted without wiring an observer into the client.

## Invariants (tests enforce most of these)

- **Core stays light.** `tests/test_layering.py` allowlists every module-scope import and bans
  `os`, `pathlib`, `subprocess`, `socket` and others at any depth. The library never reads the
  environment or opens files; sinks write through a caller-supplied callable. A bare
  `import agentloop` must not load `opentelemetry`, `httpx` or `anthropic`: OTel and metrics are
  imported lazily (see `_with_tracing` / `_with_metrics` in `agent.py`).
- **Telemetry is content-free by default.** Nothing the model, a tool or a provider wrote (including
  exception messages) reaches a summary, metric, span or trace unless the caller opts in, and
  captured text goes through the caller's `redact` function first. Summaries carry counts, names,
  statuses and error *type names* only.
- **`None` means "not reported", never zero** (`Usage`, cost, cache tokens).
- **Telemetry never becomes an execution failure.** Observer callbacks are guarded; a sink that
  raises is logged and swallowed. `conversation()` is deliberately not guarded.
- Metric attributes are low-cardinality: never conversation, correlation or parent ids.

## Conventions

- Frozen pydantic v2 models (`ConfigDict(frozen=True)`), `from __future__ import annotations`
  in every module, `collections.abc` imports, PEP 695 `type` aliases, `StrEnum` for enums.
- mypy `strict = true` over `src` **and `tests`**: annotate every test (`-> None`).
- ruff line length 100 (rules E, F, I, UP, B, SIM). pytest runs with `filterwarnings = error`.
- Sync only: the loop, clients and observers are synchronous; tools run in a thread pool, so
  anything an observer shares across tool callbacks needs a lock.
- Comments explain *why*; match the surrounding density.

## When you change public behavior

Update, in the same PR: `CHANGELOG.md` (`[Unreleased]`), the `README.md` Observability section,
the relevant `docs/api/*` and `docs/guides/*` pages, `docs/examples/*`, and `__all__` in
`src/agentloop/__init__.py`. Note any hand-written `LoopObserver` implementations this affects.

## Git and PRs

- Stage only the files your task changed; the working tree often holds unrelated edits.
- `.gitignore` has `examples/`, which also matches `docs/examples/`. Existing files there are
  tracked and stage normally, but a **new** file needs `git add -f`.
- Never push, merge or open a PR unless asked. Never commit to `main`; open a PR.
- Describe PRs as What / Why / Changes.

## Releasing

Use the `/release` skill (`.claude/skills/release/SKILL.md`; background in
`docs/guides/releasing.md`). Pushing a `vX.Y.Z` tag publishes to PyPI immediately and a version
can never be reused. Never `git push --tags`, force-push a tag or `twine upload`.
