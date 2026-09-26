#!/usr/bin/env bash
# Chapter 12. The strongest check in the tutorial: build the wheel, install it into
# a BARE virtualenv with a single provider extra, and prove it works there.
#
# Needs src/agentloop/{loop,config}.py and providers/fake.py, so it does not pass
# until chapters 7, 9 and 11 have landed. Before then it fails on the import line
# with a ModuleNotFoundError naming what is missing, which is the correct answer.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf dist
python -m build

whl=$(ls dist/agentloop-*.whl)

# A bare venv, NOT the conda environment you develop in. The conda env has every
# extra installed, so it would happily import httpx whether or not the wheel
# declares it. An empty interpreter is the only honest place to test this.
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install -q "${whl}[anthropic]"     # ONE extra only

"$tmp/venv/bin/python" - <<'PY'
import importlib.util

import agentloop.config
import agentloop.loop
import agentloop.tooling
import agentloop.tracing
import agentloop.transcript
from agentloop.providers.fake import FakeClient

assert importlib.util.find_spec("pydantic") is not None, "pydantic is a core dependency"
assert importlib.util.find_spec("httpx") is None, \
    "httpx got pulled in - the [openai-compat] extra is not actually optional"

# Tracing: the API is a hard dependency, the SDK is not. The import above proves
# agentloop.tracing ships and loads with no SDK present; these two prove the split.
assert importlib.util.find_spec("opentelemetry") is not None, \
    "opentelemetry-api is a core dependency"
assert importlib.util.find_spec("opentelemetry.sdk") is None, \
    "the OTel SDK got pulled in - the [otel] extra is not actually optional"

# D13, asserted as a CAPABILITY rather than a habit: pydantic-settings exists to
# reach os.environ, which is the thing the layering test bans `os` for. If it is
# installed, the rule is a comment.
assert importlib.util.find_spec("pydantic_settings") is None, \
    "agentloop can reach the environment - D13 is a comment, not a property"

from agentloop.config import Settings, build_client
build_client(Settings(provider="anthropic", anthropic_api_key="sk-test"))
print("ok: anthropic-only install works")
PY

"$tmp/venv/bin/python" - <<'PY'
import agentloop.config

try:
    agentloop.config.build_client(
        agentloop.config.Settings(provider="openrouter", openrouter_api_key="x")
    )
except ImportError as exc:
    assert "agentloop[openai-compat]" in str(exc), f"unhelpful error: {exc}"
    print("ok: the missing extra names the fix")
else:
    raise SystemExit("expected ImportError for the missing [openai-compat] extra")
PY

"$tmp/venv/bin/python" - <<'PY'
import agentloop.tracing

# D14, asserted as a capability rather than a promise: configure_tracing installs a
# global provider, and without the [otel] extra it cannot -- loudly, naming the fix.
try:
    agentloop.tracing.configure_tracing("http://localhost:4318")
except ImportError as exc:
    assert "agentloop[otel]" in str(exc), f"unhelpful error: {exc}"
    print("ok: tracing degrades with a message that names the extra")
else:
    raise SystemExit("expected ImportError for the missing [otel] extra")
PY

"$tmp/venv/bin/python" - <<'PY'
import sys

import agentloop

# The lazy imports in agent.py and __init__.py, asserted. A module-scope
# `from agentloop.tracing import OTelObserver` anywhere in the import graph would
# fail this, and would make "the API costs nothing at rest" untrue.
assert "opentelemetry" not in sys.modules, \
    "importing agentloop loaded OpenTelemetry - check agent.py's lazy import"
print("ok: a bare import stays cheap")
PY

unzip -l "$whl" | grep -q "agentloop/py.typed" && echo "ok: py.typed shipped"

# examples/ and scripts/ are consumers of the package, not part of it.
for excluded in examples scripts tests; do
    if unzip -l "$whl" | grep -q "${excluded}/"; then
        echo "FAIL: ${excluded}/ was swept into the wheel"; exit 1
    fi
done
echo "ok: the wheel contains the package and nothing else"
