#!/usr/bin/env bash
# Build the sdist + wheel, install the wheel into a BARE virtualenv with a single
# provider extra, and prove it works there. Run it before every tag: the source-tree
# suite cannot see packaging bugs, because the dev environment has every extra
# installed and the package itself on the path.
#
#   bash scripts/test-wheel.sh            # from a FRESH CLONE for release checks
set -euo pipefail
cd "$(dirname "$0")/.."

export PIP_DISABLE_PIP_VERSION_CHECK=1
PY=${PYTHON:-python3}
DIST=based-models-agentloop
PKG=based_models_agentloop

rm -rf dist
"$PY" -m build >/dev/null
"$PY" -m twine check dist/*

whl=$(ls dist/${PKG}-*.whl)
sdist=$(ls dist/${PKG}-*.tar.gz)

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# A bare venv, NOT the development environment (which would happily import httpx
# whether or not the wheel declares it).
"$PY" -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install -q "${whl}[anthropic]"          # ONE extra only

"$tmp/venv/bin/python" - <<'PY'
import importlib.util
from importlib.metadata import version

import agentloop
import agentloop.config
import agentloop.loop
import agentloop.tooling
import agentloop.tracing
import agentloop.transcript
from agentloop import HostedTool, join_text  # noqa: F401  public 0.2 surface
from agentloop.providers.fake import FakeClient  # noqa: F401

assert agentloop.__version__ == version("based-models-agentloop"), \
    "agentloop.__version__ and pyproject.toml disagree"
assert importlib.util.find_spec("pydantic") is not None, "pydantic is a core dependency"
assert importlib.util.find_spec("httpx") is None, \
    "httpx got pulled in - the [openai-compat] extra is not actually optional"
assert importlib.util.find_spec("opentelemetry") is not None, \
    "opentelemetry-api is a core dependency"
assert importlib.util.find_spec("opentelemetry.sdk") is None, \
    "the OTel SDK got pulled in - the [otel] extra is not actually optional"
# A capability asserted absent: pydantic-settings exists to read os.environ, which
# the layering test bans `os` for. If it is installed the rule is a comment.
assert importlib.util.find_spec("pydantic_settings") is None, \
    "agentloop can reach the environment"

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
    assert "based-models-agentloop[openai-compat]" in str(exc), f"unhelpful error: {exc}"
    print("ok: the missing extra names the fix")
else:
    raise SystemExit("expected ImportError for the missing [openai-compat] extra")
PY

"$tmp/venv/bin/python" - <<'PY'
import agentloop.tracing

try:
    agentloop.tracing.configure_tracing("http://localhost:4318")
except ImportError as exc:
    assert "based-models-agentloop[otel]" in str(exc), f"unhelpful error: {exc}"
    print("ok: tracing degrades with a message that names the extra")
else:
    raise SystemExit("expected ImportError for the missing [otel] extra")
PY

"$tmp/venv/bin/python" - <<'PY'
import sys

import agentloop

# Laziness is invisible in a diff: hoisting an import to module scope passes every
# other check. Only a fresh interpreter looking at sys.modules can see it.
assert "opentelemetry" not in sys.modules, \
    "importing agentloop loaded OpenTelemetry - check agent.py's lazy import"
for vendor in ("anthropic", "httpx"):
    assert vendor not in sys.modules, f"importing agentloop loaded {vendor}"
print("ok: a bare import stays cheap")
PY

# The full install: every provider module must at least import.
"$PY" -m venv "$tmp/full"
"$tmp/full/bin/pip" install -q "${whl}[all]"
"$tmp/full/bin/python" - <<'PY'
import importlib
import pkgutil

import agentloop.providers as providers

names = [m.name for m in pkgutil.iter_modules(providers.__path__)]
assert names, "no provider modules found"
for name in names:
    importlib.import_module(f"agentloop.providers.{name}")
print(f"ok: all {len(names)} provider modules import with [all]")
PY

contents=$(unzip -Z1 "$whl")
grep -qx "agentloop/py.typed" <<<"$contents" && echo "ok: py.typed shipped"

# License and metadata PyPI will show.
grep -q "^based_models_agentloop-.*dist-info/licenses/LICENSE$" <<<"$contents" \
    || { echo "FAIL: LICENSE is not in the wheel"; exit 1; }
meta=$(unzip -p "$whl" '*.dist-info/METADATA')
grep -q "^License-Expression: Apache-2.0" <<<"$meta" \
    || { echo "FAIL: License-Expression is not Apache-2.0"; exit 1; }
if grep -q "Private :: Do Not Upload" <<<"$meta"; then
    echo "FAIL: the Private classifier would make PyPI reject the upload"; exit 1
fi
echo "ok: license and metadata are publishable"

# Only the package and its metadata: no tests, scripts, docs or examples, in the wheel
# or the sdist.
for archive in wheel sdist; do
    if [ "$archive" = wheel ]; then listing=$contents; else listing=$(tar tzf "$sdist"); fi
    if grep -E '(^|/)(tests|scripts|docs|examples|\.github|scratch)/' <<<"$listing"; then
        echo "FAIL: unexpected files in the $archive"; exit 1
    fi
done
echo "ok: the wheel and sdist contain the package and nothing else"
