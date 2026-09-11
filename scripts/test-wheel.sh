#!/usr/bin/env bash
# Verify the built wheel installs and works with a SINGLE provider extra.
# Chapter 11 explains why this uses a bare venv rather than the conda env.
set -euo pipefail

python -m build

# A bare venv, NOT the conda environment you develop in. The conda env has every extra
# installed, so it would happily import httpx whether or not the wheel declares it.
# An empty interpreter is the only honest place to test what the wheel actually pulls in.
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install -q "$(ls dist/*.whl)[anthropic]"     # ONE extra only

"$tmp/venv/bin/python" - <<'PY'
import importlib.util

import harness, harness.loop, harness.tools, harness.transcript, harness.config
from harness.providers.fake import FakeClient

assert importlib.util.find_spec("pydantic") is not None, "pydantic is a core dependency"
assert importlib.util.find_spec("httpx") is None, \
    "httpx got pulled in — the [openai] extra is not actually optional"

from harness.config import Settings, build_client
build_client(Settings(provider="anthropic", anthropic_api_key="sk-test"))
print("ok: anthropic-only install works")
PY

"$tmp/venv/bin/python" - <<'PY'
import harness.config

try:
    harness.config.build_client(
        harness.config.Settings(provider="openrouter", openrouter_api_key="x"))
except ImportError as e:
    assert "harness[openai]" in str(e), f"unhelpful error: {e}"
    print("ok: missing extra names the fix")
else:
    raise SystemExit("expected ImportError for the missing [openai] extra")
PY

unzip -l dist/*.whl | grep -q "harness/py.typed" && echo "ok: py.typed shipped"
