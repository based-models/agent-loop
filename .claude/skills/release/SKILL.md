---
name: release
description: Cut a PyPI release of based-models-agentloop by pushing a version tag. Use only when the user invokes /release.
disable-model-invocation: true
---

# Release based-models-agentloop to PyPI

Distribution `based-models-agentloop`, import name `agentloop`, repo `based-models/agent-loop`.

Pushing a `vX.Y.Z` tag **publishes to PyPI immediately** (Trusted Publishing, no approval
gate), and a PyPI version number can **never be reused**, even after deletion. Everything
before the tag is preparation; the tag is the point of no return. The explanation lives in `docs/guides/releasing.md`; read it if anything here is unclear
and do not duplicate it.

## 1. Preconditions (stop and report on any failure; do not fix silently)
Run read-only checks first:
- On `main`, up to date with `origin/main` (`git fetch`, then compare). Unrelated local
  edits that do not belong in the release: leave them alone and say so.
- CI on the release commit is green: `gh run list --branch main --limit 3`.
- Version `X.Y.Z` is identical in `pyproject.toml` and `src/agentloop/__init__.py`
  (`__version__`). The release workflow rejects a mismatch, but catch it earlier.
- `CHANGELOG.md` has a `## [X.Y.Z]` heading for exactly this version.
- No `vX.Y.Z` tag exists locally or on origin (`git ls-remote --tags origin`), and
  `https://pypi.org/pypi/based-models-agentloop/X.Y.Z/json` is a 404 (that version is unpublished).
- README or doc changes meant for the PyPI page are **merged to `main`**, because the tag
  publishes what is on `main`.

If the version is not bumped yet, offer to do the bump (pyproject, `__init__`, changelog) on a
branch and open a PR. Never push version bumps straight to `main`.

## 2. Confirm before tagging
Tell the user exactly what will happen and wait for a clear yes in this conversation:
> Tagging `vX.Y.Z` on `agent-loop` at commit `<short sha>` will publish `based-models-agentloop X.Y.Z` to PyPI
> immediately. That version can never be re-uploaded. Proceed?

A yes to an earlier question, or to a different release, does not count.

## 3. Tag (only after the yes)
```bash
git tag vX.Y.Z && git push origin vX.Y.Z      # one tag, by name; never `git push --tags`
```

## 4. Watch, then verify
- Find the run: `gh run list --workflow release.yml --limit 1`, then wait until it
  completes (a background watch, not a sleep loop). Report each job: `build`,
  `publish-pypi`, `github-release`.
- Verify from the real index in a throwaway venv outside the repo:
  ```bash
  python3 -m venv "$TMP/v" && "$TMP/v/bin/pip" install 'based-models-agentloop[anthropic]==X.Y.Z'
  "$TMP/v/bin/python" -c "import agentloop; print(agentloop.__version__)"
  ```
- Confirm the GitHub Release has the wheel, sdist and `SHA256SUMS`:
  `gh release view vX.Y.Z --json assets`.

## 5. If something fails
- Fails before `publish-pypi` uploads (tests, version check, missing pending publisher):
  nothing was published and the version is not used. Fix, then delete and re-push the tag
  **only if the user agrees**, since re-pushing re-triggers a publish.
- `publish-pypi` succeeded but the release is bad: **do not** move or delete the tag.
  Recommend yanking `X.Y.Z` on PyPI (the user does this in the PyPI web UI) and releasing
  `X.Y.(Z+1)`.

## Never
- Tag without the explicit confirmation in step 2.
- `git push --tags`, force-push a tag, or reuse a version number.
- Add API tokens or `twine upload`: publishing is only via the workflow.
- Release from anything but an up-to-date `main`.

## Related package
`based-models-agentloop-tools` (repo `agent-loop-tools`) depends on this one from PyPI. After
releasing a new agent-loop version, the tools package can adopt it: its dependency range lives
in its `pyproject.toml`, and its CI fails until the needed version exists on PyPI.
