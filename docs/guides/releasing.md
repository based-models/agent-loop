# Releasing

Releases are cut from `main` by pushing a `vX.Y.Z` tag. The pipeline publishes to PyPI with
Trusted Publishing (OIDC); there are no API tokens to store or rotate. There is no approval
gate (GitHub Free private repos cannot require reviewers), so **pushing the tag publishes**:
run the local checklist below first, and treat tag creation as the release decision.

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

The `Release` workflow then:

1. **build**: refuses to continue unless the tag, `pyproject.toml` and `__version__` agree
   and the changelog has an entry; runs ruff, mypy, pytest and `scripts/test-wheel.sh`;
   builds the wheel and sdist once and uploads them as an artifact.
2. **publish-pypi**: uploads those exact files to PyPI via Trusted Publishing, with
   attestations.
3. **github-release**: attaches the same files plus `SHA256SUMS` to the GitHub release.

## One-time setup

- PyPI (and TestPyPI) account with 2FA.
- A **pending publisher** for the project on each: owner `based-models`, repository
  `agent-loop`, workflow `release.yml`, environment `pypi` (TestPyPI: `testpypi`). The first
  successful publish creates the project, so the name is reserved only at that moment:
  register the pending publisher promptly.
- GitHub environments named `pypi` and `testpypi`. They must exist with exactly those names
  because the PyPI trusted publisher matches on the environment name. (Required reviewers
  are unavailable on private Free-plan repos, so they are not used.)

## Dry run

Actions -> Release -> **Run workflow** (on any branch) builds, tests and publishes to
TestPyPI only. Run it from the version-bump branch: TestPyPI refuses to re-upload a version,
and the previous version is usually already there, so a run from `main` fails until the
version is bumped. Check `https://test.pypi.org/pypi/based-models-agentloop/X.Y.Z/json` is a
404 first; to retry after an upload, bump the version. Verify in a clean venv:

```bash
pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ 'based-models-agentloop[anthropic]==X.Y.Z'
```

Use `--extra-index-url` only for this check: TestPyPI does not carry pydantic and friends.

## What `scripts/test-wheel.sh` proves

It builds the wheel, installs it into a bare virtualenv with a single extra, and checks
what only an installed wheel can show: optional extras really are optional (`httpx`, the
OTel SDK are absent), the library cannot read the environment (`pydantic_settings` absent),
missing extras fail with a message naming the fix, `import agentloop` stays cheap (no
OpenTelemetry, no vendor SDK), every provider module imports under `[all]`, `py.typed`
ships, and no tests/docs/examples leak into the wheel or sdist.

## Rolling back

PyPI files are immutable and a version number can never be reused, even after deletion.
If a release is bad, **yank** it on PyPI (installs of a pinned version still work, fresh
resolves skip it), fix forward, and release `X.Y.(Z+1)`. Never move a published tag.

## Versioning

SemVer. While `0.x`, a minor bump may break `agentloop.__all__`. The layering tests
(`tests/test_layering.py`) are the guardrail on what the library may import.
