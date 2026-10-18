#!/usr/bin/env bash
# Verify a PUBLISHED release end to end. Run it after the tag-triggered Release workflow has
# finished:
#
#   bash scripts/verify-release.sh 0.5.0
#   PYTHON=/path/to/python3.12 bash scripts/verify-release.sh 0.5.0
#
# Prints one `ok:` or `FAIL:` line per check and exits non-zero if any failed. Needs `gh`
# (authenticated), `curl`, network access, and installs the release from PyPI into a throwaway
# venv outside the repository.
set -uo pipefail

VERSION=${1:-}
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 X.Y.Z" >&2; exit 2; }
TAG="v$VERSION"
DIST=based-models-agentloop
PKG=based_models_agentloop
PY=${PYTHON:-python3}
failed=0

ok()   { echo "ok: $*"; }
fail() { echo "FAIL: $*"; failed=1; }

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# 1. The tag-triggered run finished and every job that should have run did.
run_id=$(gh run list --workflow release.yml --branch "$TAG" --limit 1 \
    --json databaseId --jq '.[0].databaseId' 2>/dev/null)
if [[ -z "$run_id" ]]; then
    fail "no Release workflow run found for tag $TAG"
else
    jobs=$(gh run view "$run_id" --json status,jobs \
        --jq '.status as $s | "\($s)|" + ([.jobs[] | "\(.name)=\(.conclusion)"] | join(","))')
    status=${jobs%%|*}; results=${jobs#*|}
    if [[ "$status" != completed ]]; then
        fail "Release run $run_id is still $status; wait for it to finish"
    else
        for job in build publish-pypi github-release; do
            [[ ",$results," == *",$job=success,"* ]] \
                && ok "workflow job $job succeeded" || fail "workflow job $job: $results"
        done
    fi
fi

# 2. PyPI serves both files. The simple index is the source of truth; the JSON API sits
#    behind a cache and can show a stale "latest" for several minutes after a publish.
found=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    index=$(curl -fsS -H 'Cache-Control: no-cache' \
        "https://pypi.org/simple/$DIST/?cb=$(date +%s)" 2>/dev/null || true)
    if grep -q "${PKG}-${VERSION}-py3-none-any.whl" <<<"$index" \
        && grep -q "${PKG}-${VERSION}.tar.gz" <<<"$index"; then found=1; break; fi
    sleep 6
done
[[ $found == 1 ]] && ok "PyPI lists the wheel and sdist for $VERSION" \
    || fail "PyPI does not list both files for $VERSION after ~1 minute"

# 3. The GitHub release is public and carries the files.
assets=$(gh release view "$TAG" --json isDraft,isPrerelease,assets \
    --jq '"\(.isDraft)|\(.isPrerelease)|" + ([.assets[].name] | join(","))' 2>/dev/null || true)
if [[ -z "$assets" ]]; then
    fail "no GitHub release for $TAG"
else
    IFS='|' read -r draft pre names <<<"$assets"
    [[ "$draft" == false && "$pre" == false ]] \
        && ok "GitHub release $TAG is public" || fail "release is draft=$draft prerelease=$pre"
    for want in "${PKG}-${VERSION}-py3-none-any.whl" "${PKG}-${VERSION}.tar.gz" SHA256SUMS; do
        [[ ",$names," == *",$want,"* ]] && ok "release asset $want" || fail "missing asset $want"
    done
fi

# 4. A clean install of what PyPI serves works. Run from outside the repo, so the source tree
#    cannot shadow the installed wheel.
if "$PY" -m venv "$tmp/venv" \
    && "$tmp/venv/bin/pip" install -q --no-cache-dir "${DIST}[anthropic]==${VERSION}" 2>"$tmp/pip.err"; then
    (cd "$tmp" && "$tmp/venv/bin/python" - "$VERSION" <<'PY'
import sys
from importlib.metadata import version

import agentloop
from agentloop import Agent
from agentloop.provider import ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.transcript import TextPart

want = sys.argv[1]
assert version("based-models-agentloop") == agentloop.__version__ == want, \
    (version("based-models-agentloop"), agentloop.__version__, want)
assert "site-packages" in agentloop.__file__, agentloop.__file__
loaded = [m for m in ("opentelemetry", "httpx", "anthropic") if m in sys.modules]
assert not loaded, f"a bare import loaded {loaded}"

reply = ModelResponse(content=(TextPart(text="hi"),), stop_reason=StopReason.END_TURN,
                      usage=Usage(input_tokens=1, output_tokens=1), model="m", provider="fake")
result = Agent([], client=FakeClient(reply)).run_result("go")
assert result.text == "hi" and result.summary.succeeded
PY
    ) && ok "clean install of $VERSION imports and runs" || fail "installed $VERSION failed its smoke test"
else
    fail "could not install ${DIST}==${VERSION}: $(grep -i 'error' "$tmp/pip.err" | tail -n 1)"
fi

echo
[[ $failed == 0 ]] && echo "ok: $TAG verified" || echo "FAIL: $TAG did not verify"
exit "$failed"
