# Installing

based-models-agentloop is private. It is installed straight from its GitHub repository and
is never uploaded to PyPI or any other index.

## Why not an index

The name `agentloop` on public PyPI belongs to an unrelated project. With an index in the
picture, `pip` can pick that one (highest version wins) instead of ours: a dependency
confusion attack, without anyone attacking. A direct URL reference removes the index from
the decision entirely, and the distribution name `based-models-agentloop` means a mistyped
bare `pip install` finds nothing of ours to confuse.

## Install

Pin an immutable **commit SHA**. Tags can be moved; a SHA cannot.

```text
# requirements.txt
based-models-agentloop[anthropic] @ git+ssh://git@github.com/based-models/agent-loop.git@<commit-sha>
```

Releases are tagged `vX.Y.Z`; find the SHA with `git rev-parse vX.Y.Z^{commit}`.

## What is and is not protected

- The direct reference is protected. Its **transitive** dependencies (pydantic, anthropic,
  ...) still resolve from PyPI, which is normal and fine.
- To pin those too, compile a hashed lockfile in the consuming project (leave
  `based-models-agentloop` out of `requirements.in`; pip cannot hash a git checkout) and
  install in two steps:

  ```bash
  pip-compile --generate-hashes requirements.in -o requirements.lock
  pip install --require-hashes -r requirements.lock
  pip install --no-deps 'based-models-agentloop[anthropic] @ git+ssh://git@github.com/based-models/agent-loop.git@<commit-sha>'
  ```

  For a content hash on our own package too, install the release wheel instead (below).
- The `Private :: Do Not Upload` classifier makes PyPI reject an accidental upload.

## Authentication

- Locally: your SSH key.
- In CI: a read-only **deploy key** on `based-models/agent-loop`, loaded with an SSH agent
  action, or a fine-grained read-only token supplied through git config. Never embed a
  token in the URL that is committed to a requirements file.

```yaml
- uses: webfactory/ssh-agent@dc588b651fe13675774614f8e6a936a468676387  # v0.9.0
  with:
    ssh-private-key: ${{ secrets.AGENT_LOOP_DEPLOY_KEY }}
- run: pip install -r requirements.txt
```

## Install from a release wheel

Each GitHub Release carries the wheel, the sdist and `SHA256SUMS`:

```bash
gh release download v0.3.0 -R based-models/agent-loop -p '*.whl' -p SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS
pip install ./based_models_agentloop-0.3.0-py3-none-any.whl[anthropic]
```
