#!/usr/bin/env bash
# Everything CI asks of a change, in one command: lint, types, tests with the coverage gate,
# and a bare-import check. Same checks as the `test` job in .github/workflows/ci.yml and the
# `build` job in release.yml, which run them inline; keep the three in step.
#
#   bash scripts/check.sh              # full
#   bash scripts/check.sh --no-cov     # skip coverage for a quicker loop
#   PYTHON=/path/to/python bash scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python}
cov=(--cov --cov-fail-under=88)
case "${1:-}" in
    "") ;;
    --no-cov) cov=() ;;
    *) echo "usage: scripts/check.sh [--no-cov]" >&2; exit 2 ;;
esac

step() { printf '\n==> %s\n' "$1"; }

step "ruff"
"$PY" -m ruff check .

step "mypy"
"$PY" -m mypy

step "pytest ${cov[*]:-(no coverage)}"
"$PY" -m pytest -q "${cov[@]}"

# Laziness is invisible in a diff: hoisting an import to module scope passes every other
# check. Only a fresh interpreter looking at sys.modules can see it.
step "a bare import stays cheap"
"$PY" - <<'PY'
import sys

import agentloop

heavy = [m for m in ("opentelemetry", "httpx", "anthropic") if m in sys.modules]
assert not heavy, f"importing agentloop loaded {heavy}; check for a module-scope import"
print("ok: importing agentloop loads no OpenTelemetry or vendor SDK")
PY

printf '\nok: all checks passed\n'
