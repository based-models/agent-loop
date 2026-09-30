# Releasing

Releases are cut from `main` by pushing a `vX.Y.Z` tag. Nothing is uploaded to an index.

## Checklist

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new `## [X.Y.Z]` heading.
2. Set the version in **both** `pyproject.toml` and `src/agentloop/__init__.py`.
3. Locally, from a fresh clone (your working tree hides missing files):

   ```bash
   git clone . /tmp/agentloop-release && cd /tmp/agentloop-release
   pip install -e '.[all,dev]'
   ruff check . && mypy && pytest -q --cov --cov-fail-under=88
   bash scripts/test-wheel.sh
   ```

4. Tag and push:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

The `Release` workflow re-runs the checks, refuses to continue unless the tag, `pyproject.toml`
and `__version__` agree and the changelog has an entry, runs `scripts/test-wheel.sh`, and
publishes a GitHub Release with the wheel, sdist and `SHA256SUMS`.

## What `scripts/test-wheel.sh` proves

It builds the wheel, installs it into a bare virtualenv with a single extra, and checks
what only an installed wheel can show: optional extras really are optional (`httpx`, the
OTel SDK are absent), the library cannot read the environment (`pydantic_settings` absent),
missing extras fail with a message naming the fix, `import agentloop` stays cheap (no
OpenTelemetry, no vendor SDK), every provider module imports under `[all]`, `py.typed`
ships, and no tests/docs/examples leak into the wheel or sdist.

## Rolling back

Never move or delete a published tag that anyone may have pinned. Delete the GitHub Release
if needed, fix forward, and release `X.Y.(Z+1)`.

## Versioning

SemVer. While `0.x`, a minor bump may break `agentloop.__all__`. The layering tests
(`tests/test_layering.py`) are the guardrail on what the library may import.
