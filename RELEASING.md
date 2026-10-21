# Releasing

Create release tags from the intended commit on `main`. Pushing a `vX.Y.Z` tag starts the
release workflow, which builds the package and publishes it to PyPI through Trusted
Publishing. The workflow does not check which branch contains the tagged commit. Complete
the checks below before pushing the tag.

## Checklist

1. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new `## [X.Y.Z]` heading.
2. Set the version in **both** `pyproject.toml` and `src/agentloop/__init__.py`.
3. In a fresh clone, use Python 3.12 or newer to run the checks and test the built package:

   ```bash
   git clone . /tmp/agentloop-release && cd /tmp/agentloop-release
   python -m pip install -e '.[all,dev]'
   bash scripts/check.sh
   bash scripts/test-wheel.sh
   ```

4. Once the checks pass, create and push the tag for that commit:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

The `Release` workflow then:

1. **build**: refuses to continue unless the tag, `pyproject.toml` and `__version__` agree
   and the changelog has an entry; runs ruff, mypy, pytest and `scripts/test-wheel.sh`;
   builds the wheel and sdist once and uploads them as an artifact.
2. **publish-pypi**: uploads those exact files to PyPI via Trusted Publishing. PyPI records
   provenance attestations for the published files.
3. **github-release**: attaches the same files plus `SHA256SUMS` to the GitHub release.

## Publishing setup

- The PyPI and TestPyPI projects need trusted publishers for owner `based-models`, repository
  `agent-loop`, workflow `release.yml`, and their respective environments: `pypi` and
  `testpypi`.
- The GitHub repository needs environments named `pypi` and `testpypi` to match those
  publisher configurations. Check their settings before starting a release.

## TestPyPI rehearsal

In GitHub Actions, select **Release** then **Run workflow** on the version-bump branch. This
manual run builds and tests the package, then publishes to TestPyPI only. Choose a version
that has not been uploaded there before; an uploaded version cannot be reused. Check the
TestPyPI release page before running it, then install the result in a clean environment:

```bash
python -m pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ 'based-models-agentloop[anthropic]==X.Y.Z'
```

The extra index lets pip fetch dependencies that are not available on TestPyPI. Use a
version unique to TestPyPI so the command does not select the same version from PyPI.

## What `scripts/test-wheel.sh` proves

It builds the wheel and source distribution, then installs the wheel into clean virtual
environments. An install with only `anthropic` checks that `httpx` and the OpenTelemetry SDK
remain optional and that missing extras produce useful errors. A separate install with `all`
checks provider imports. The script also checks `py.typed`, license metadata, and package
contents. It deletes and recreates the clone's `dist/` directory.

## Rolling back

PyPI files are immutable and a version number cannot be reused. If a release is broken,
[yank it on PyPI](https://docs.pypi.org/project-management/yanking/), fix the issue, and
publish a new version. Installers usually skip yanked releases, but an exact `==` or `===`
pin may still select one. Do not move a published tag.

## Versioning

This project uses SemVer. While the version is `0.x`, a minor bump may change the public API
exposed through `agentloop.__all__`.
