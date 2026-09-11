# 11 — Testing

Every chapter had a checkpoint. This one adds the tests that prove the *architecture* rather than any single function — and the two that catch the failures nothing else can.

---

## `FakeClient`

The seam is one method, so the fake is one class.

`src/harness/providers/fake.py` — shipped in the package, not in `tests/`, so consumers can use it too:

```python
class FakeClient:
    """Replays scripted responses. Records every request. Zero dependencies."""

    name = "fake"

    def __init__(self, *responses: ModelResponse | Exception,
                 model: str = "fake-1") -> None:
        self.model = model
        self._queue: list[ModelResponse | Exception] = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._queue:
            raise AssertionError(
                f"FakeClient exhausted after {len(self.requests)} requests; "
                "the loop asked for more than the script provides"
            )
        nxt = self._queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt                     # script retries and error paths too
        return nxt
```

Three properties that matter:

**No base class, no registration.** Chapter 3 chose `Protocol` over ABC; this is the payoff. `FakeClient` imports `ModelResponse` for typing and nothing else.

**It records requests.** Half the assertions you want are about what the loop *sent* — did `max_tokens` go up after a truncation, did the tool schemas come through, is `temperature` absent.

**A queued `Exception` is raised.** That's what makes chapter 8's retry paths testable without patching `httpx`.

The exhaustion message is worth the three lines. `IndexError: pop from empty list` tells you nothing; "the loop asked for more than the script provides" tells you the loop didn't terminate when you expected.

---

## Round-trip translation tables

Chapters 4 and 5 tested translation ad hoc. Make it a table so adding a provider means adding rows.

```python
from harness.providers import anthropic
from harness.providers import _openai_compat as openai_compat
from harness.providers._openai_compat import Quirks

CASES = [
    # (name, canonical Message, expected Anthropic block, expected OpenAI message(s))
    (
        "assistant text + tool call",
        Message("assistant", (
            TextPart("checking"),
            ToolCallPart(id="c1", name="read", arguments={"path": "a.py"}),
        )),
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "c1", "name": "read",
             "input": {"path": "a.py"}},
        ]},
        [{"role": "assistant", "content": "checking", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read", "arguments": '{"path": "a.py"}'}}]}],
    ),
    (
        "two tool results in one user message",
        Message("user", (
            ToolResultPart(call_id="c1", name="read", content="a"),
            ToolResultPart(call_id="c2", name="read", content="b"),
        )),
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "a",
             "is_error": False},
            {"type": "tool_result", "tool_use_id": "c2", "content": "b",
             "is_error": False},
        ]},
        [{"role": "tool", "tool_call_id": "c1", "name": "read", "content": "a"},
         {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "b"}],
    ),
]


@pytest.mark.parametrize("name,canonical,anthropic_wire,openai_wire",
                         CASES, ids=[case[0] for case in CASES])
def test_translation(name, canonical, anthropic_wire, openai_wire):
    assert anthropic.message_to_wire(canonical) == anthropic_wire
    assert openai_compat.message_to_wire(canonical, Quirks()) == openai_wire
```

> **Python note.** `@pytest.mark.parametrize` runs the same test once per row in the
> list, unpacking each tuple into the named arguments. One test function, one failure
> per broken row, and adding a case means adding a tuple. `ids=` just gives each run a
> readable name in the output.

The second case is the asymmetry that justified the whole canonical-shape decision, pinned as a test: **one message in, two messages out**, in call order.

---

## Recorded fixtures

The tests that catch real-world weirdness. Save one genuine response body per provider as JSON and feed it to the adapter — no network, and you can reproduce a server's bad day forever.

```
tests/fixtures/
  anthropic_tool_use.json
  anthropic_thinking.json
  openrouter_parallel_calls.json
  openrouter_error_in_200.json          ← a 200 that is not success
  vllm_stop_with_tool_calls.json        ← finish_reason "stop", tool_calls populated
  vllm_no_usage_details.json            ← prompt_tokens_details absent
  vllm_context_overflow_400.json        ← "code": 400 as an INTEGER
  modal_proxy_401.txt                   ← PLAIN TEXT, not JSON
```

```python
def test_vllm_stop_with_tool_calls_still_reads_as_tool_use():
    body = json.loads((FIXTURES / "vllm_stop_with_tool_calls.json").read_text())
    resp = _client()._from_wire(body)
    assert resp.stop_reason is StopReason.TOOL_USE     # Rule 1
    assert resp.raw_stop_reason == "stop"


def test_modal_proxy_401_is_not_json():
    text = (FIXTURES / "modal_proxy_401.txt").read_text()
    r = httpx.Response(401, text=text,
                       request=httpx.Request("POST", "http://x/v1/chat/completions"))
    err = classify_http_error(r, provider="modal", quirks=Quirks(errors_are_json=False))
    assert isinstance(err, AuthError) and "missing credentials" in str(err)
```

Collect them as you go — when you hit something odd in chapter 9, save the body before you fix it. Those four `vllm_*` / `modal_*` fixtures are cases you cannot construct by reasoning about the spec, because they *violate* the spec.

---

## Golden transcripts

Chapter 2's redundant `type` field buys this with no serialization library:

```python
def test_golden_bugfix_conversation(tmp_path, snapshot_path):
    client = FakeClient(*load_script("bugfix"))
    transcript = Transcript()
    transcript.add_user_text("buggy.py crashes, fix it")
    run_conversation(transcript, client, Registry([READ, EDIT, BASH]),
                     Deps(cwd=tmp_path))

    actual = transcript.model_dump_json(indent=2)
    if os.environ.get("UPDATE_GOLDEN"):
        snapshot_path.write_text(actual)
    # Compare parsed models, not raw text: key order and whitespace are not the contract.
    assert Transcript.model_validate_json(actual) == \
           Transcript.model_validate_json(snapshot_path.read_text())
```

Comparing *parsed* transcripts rather than strings is worth the extra line. A reordered
field or a changed indent would otherwise fail a test that nothing has actually broken,
and you would learn to regenerate goldens reflexively — which is how a golden test stops
testing anything.

These catch changes you didn't mean to make — a tool result gaining a field, an ordering shift, a schema key renamed. `UPDATE_GOLDEN=1 pytest` regenerates. **Read the diff before you commit it**; a golden test you regenerate reflexively is a golden test that tests nothing.

---

## The seam test

Twelve lines, and the most valuable ones in the repo.

```python
# NOT pydantic: core models its own data with it. The rule is "core knows about no
# specific LLM provider", not "core has no dependencies". See chapter 3.
BANNED = {"anthropic", "openai", "httpx", "requests", "openrouter", "modal"}
CORE = Path(__file__).parent.parent / "src" / "harness"


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_has_no_vendor_imports(path):
    """Core is transcript/provider/errors/tools/loop/retry/config/cli/tracing.
    Vendor SDKs live ONLY under providers/."""
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert not (roots & BANNED), f"{path.name} imports {sorted(roots & BANNED)}"
```

`CORE.glob("*.py")` is non-recursive on purpose — `providers/` is exactly where these imports belong.

Two modules need a carve-out and it's worth understanding why each is fine:

- **`tracing.py`** imports `opentelemetry`, which isn't in `BANNED`. Deliberate: the API package is a no-op null object (chapter 10), so it doesn't leak a vendor *model* concept upward.
- **`config.py`** imports provider modules — but **inside `match` branches**, which `ast.walk` still finds. Extend the check rather than exempting the file:

```python
def module_level_imports(tree: ast.Module) -> set[str]:
    """Root package names imported at module scope.

    Deliberately NOT ast.walk: an import nested inside a function is a lazy import,
    which is exactly what we want adapters to use.
    """
    roots: set[str] = set()
    for node in tree.body:                          # top level only
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots
```

> **Python note.** `ast.parse(source)` gives you the file's syntax tree without running
> it. `tree.body` is the list of top-level statements; `ast.walk(tree)` visits *every*
> node at any depth. The difference between those two is the entire test: a top-level
> `import anthropic` shows up in `tree.body`, and one tucked inside a function does not.
> `node.level == 0` skips relative imports like `from .transcript import Message`, which
> are our own modules and always fine.

That distinction is precisely the property you want: a top-level `import anthropic` in `config.py` breaks a Modal-only install; one inside `case "anthropic":` does not.

### Why `pydantic` is not on that list

The rule this test encodes is **"core knows about no specific LLM provider"**, not "core
has no dependencies." Pydantic is a data-modelling library: it has no opinion about
Anthropic or vLLM, it does not drift with a provider's API, and it does not make a
Modal-only install pull in the Anthropic SDK. `anthropic` and `httpx` are how you talk to
one vendor. Those are different kinds of dependency, and the banned list is where you
write down which kind you refuse.

If that line ever feels arbitrary, the test to imagine is the one below: **can someone
install the wheel with a single provider extra and use it?** Pydantic does not threaten
that. A stray `import httpx` in `errors.py` does.

### Why this test rather than a code review rule

The seam degrades one convenient import at a time, and every individual one is defensible. `from anthropic.types import Message` in `transcript.py` "just for a type hint." `import httpx` in `errors.py` "to catch `httpx.TimeoutException`." Each is a five-minute shortcut; together they mean the loop knows about vendors, tests need network mocks, and a Modal-only install crashes on import.

A test says no in CI, every time, at the moment of the shortcut rather than six months later.

---

## The packaging test

The strongest check in the tutorial, and the only one that catches an eager import in an adapter.

`scripts/test-wheel.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m build

# A bare venv, NOT the conda environment you develop in. The conda env has every extra
# installed, so it would happily import httpx whether or not the wheel declares it.
# An empty interpreter is the only honest place to test what the wheel actually pulls in.
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install -q "$(ls dist/*.whl)[anthropic]"     # ONE extra only

"$tmp/venv/bin/python" - <<'PY'
import harness, harness.loop, harness.tools, harness.transcript, harness.config
from harness.providers.fake import FakeClient

import importlib.util
assert importlib.util.find_spec("pydantic") is not None, "pydantic is a core dependency"
assert importlib.util.find_spec("httpx") is None, \
    "httpx got pulled in — the [openai] extra is not actually optional"

from harness.config import Settings, build_client
build_client(Settings(provider="anthropic", anthropic_api_key="sk-test"))
print("ok: anthropic-only install works")
PY

"$tmp/venv/bin/python" -c "
import harness.config
try:
    harness.config.build_client(
        harness.config.Settings(provider='openrouter', openrouter_api_key='x'))
except ImportError as e:
    assert 'harness[openai]' in str(e), f'unhelpful error: {e}'
    print('ok: missing extra names the fix')
else:
    raise SystemExit('expected ImportError for the missing [openai] extra')
"

unzip -l dist/*.whl | grep -q "harness/py.typed" && echo "ok: py.typed shipped"
```

Four properties, none of which any unit test can check:

1. **The wheel installs and imports** with only the `[anthropic]` extra.
2. **`httpx` is genuinely absent** — the `[openai]` extra is really optional. A stray top-level `import httpx` in `errors.py` or `config.py` fails here.
3. **The missing-extra error names the fix**, so a consumer gets `install 'harness[openai]'` and not a bare `ModuleNotFoundError`.
4. **`py.typed` shipped**, so consumers actually get your types (chapter 1).

Run it in CI. It is the test that keeps chapter 1's optional-extras promise true after
six months of edits.

### Why this can't be done in your conda environment

Your development environment was created with `-e .[all,dev]`, so it has `anthropic`,
`httpx`, the OTel SDK, pytest, and mypy all installed. Every import succeeds there
whether or not the wheel declares it. The environment that proves something is the one
with *nothing* in it.

That is also the answer to "should the test create a conda env instead?" It could —
`conda create -n wheeltest python=3.12 pip` works identically — but `python -m venv` is
faster, needs no conda on the CI runner, and the point is a minimal interpreter rather
than a managed environment.

---

## What the full suite covers

| Test | Catches |
|---|---|
| Transcript invariants (ch. 2) | Unpaired tool calls, before they reach the API |
| Protocol satisfaction (ch. 3) | A seam that needs a base class |
| Translation tables (ch. 4, 5, 11) | Every wire asymmetry |
| Recorded fixtures (11) | Server behavior that violates the spec |
| Totality of `run_tools` (ch. 6) | The unpaired-`tool_use` 400, at its source |
| Loop failure modes (ch. 7) | F1, F10, stop-reason handling |
| Retry classification (ch. 8) | Retrying a 400; not retrying a 529 |
| Span shape (ch. 10) | Telemetry drift, and content leaking into traces |
| **Golden transcripts** | Changes you didn't mean to make |
| **AST seam test** | The architecture eroding one import at a time |
| **Wheel install** | Optional extras that aren't |

```bash
conda activate harness
pytest -q && mypy && ruff check . && bash scripts/test-wheel.sh
```

Green across all four and the harness holds.

---

## What is deliberately not tested

- **Live provider calls in CI.** Slow, flaky, costs money, and fails for reasons that aren't your bug. Chapter 9's cross-provider run is a *manual* checkpoint. If you want it automated, make it a separate nightly job that is allowed to fail.
- **Model quality.** "Does the agent fix the bug" is an eval, not a unit test. Different discipline, different tooling, different cadence.
- **`bash` sandboxing.** There isn't any. Chapter 12.

---

Next: [12-next-seams.md](12-next-seams.md) — where everything deferred attaches.
