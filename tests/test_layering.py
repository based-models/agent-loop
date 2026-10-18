"""The architecture, asserted.

Two seams, shaped differently because the questions are:

* provider seam: "does core know a specific LLM vendor?" -- a CLOSED question, so a
  denylist works.
* use-case seam: "does the library reach the machine?" -- an OPEN question, so core
  gets an allowlist. Growing it costs a line of diff somebody has to approve.

Both are parametrised per file: a new module arrives with its assertions attached.
"""

import ast
import importlib.util
from collections.abc import Iterable
from pathlib import Path

import pytest

from agentloop.provider import LLMClient, ModelRequest, ModelResponse, StopReason, Usage
from agentloop.providers.fake import FakeClient
from agentloop.transcript import TextPart

CORE = Path(__file__).parent.parent / "src" / "agentloop"
SOURCES = sorted(CORE.rglob("*.py"))
IDS = [str(path.relative_to(CORE)) for path in SOURCES]

# --- seam 1: vendors. NOT pydantic (data modelling, no provider opinion) and NOT
# opentelemetry (its API package is a no-op without an SDK).
BANNED = {"anthropic", "openai", "httpx", "httpx2", "requests"}

# --- seam 2: the machine. Every root package agentloop may import at module scope.
# If a new import touches the filesystem, a process or a socket, it does not belong
# here: it belongs in an application.
CORE_ALLOWED = {
    "__future__",
    "agentloop",
    # stdlib
    "collections",      # Counter, abc
    "concurrent",       # ThreadPoolExecutor, in the tool executor
    "contextlib",       # nullcontext / contextmanager, observers and tracing
    "enum",             # StopReason
    "hashlib",          # stall detection compares tool calls by hash, in memory only
    "inspect",          # signature().bind(), tool executor and @tool
    "json",             # canonical tool arguments (stall) and event-trace lines
    "logging",
    "random",           # retry jitter
    "threading",        # tracing's provider-install lock
    "time",             # loop deadline, retry backoff
    "typing",
    "uuid",             # conversation ids
    # declared dependencies -- keep in sync with pyproject.toml
    "opentelemetry",
    "pydantic",
}

# providers/ additionally gets the wire.
PROVIDER_ALLOWED = CORE_ALLOWED | {
    "anthropic", "httpx", "httpx2",  # httpx2: the HTTP stack under the Anthropic SDK
    "dataclasses",      # Quirks in the OpenAI-compatible adapter
    "json",             # wire (de)serialisation
    "types",            # SimpleNamespace
}

# Banned at ANY depth, including inside functions and `if TYPE_CHECKING:`. There is
# no legitimate use of these in the library, so there is nowhere to hide them.
NEVER = {"subprocess", "os", "shutil", "socket", "glob", "tempfile", "pathlib"}


def import_roots(nodes: Iterable[ast.AST]) -> set[str]:
    roots: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots       # level > 0 is a relative import: always our own


def module_level_imports(tree: ast.Module) -> set[str]:
    """Deliberately NOT ast.walk: a lazy import inside a function is correct."""
    return import_roots(tree.body)


def is_provider_module(path: Path) -> bool:
    return path.parent.name == "providers"


# --- the provider seam ---


@pytest.mark.parametrize("path", sorted(CORE.glob("*.py")), ids=lambda p: p.name)
def test_core_has_no_vendor_imports(path: Path) -> None:
    """Core is everything outside providers/. Vendor SDKs live only in there."""
    found = module_level_imports(ast.parse(path.read_text())) & BANNED
    assert not found, f"{path.name} imports {sorted(found)} at module scope"


@pytest.mark.parametrize("path", sorted((CORE / "providers").glob("*.py")),
                         ids=lambda p: p.name)
def test_adapters_import_their_sdk_lazily(path: Path) -> None:
    """An adapter may use its SDK, but not at import time: extras stay optional."""
    found = module_level_imports(ast.parse(path.read_text())) & BANNED
    assert not found, f"providers/{path.name} imports {sorted(found)} at module scope"


# --- the use-case seam ---


@pytest.mark.parametrize("path", SOURCES, ids=IDS)
def test_module_imports_are_on_the_allowlist(path: Path) -> None:
    allowed = PROVIDER_ALLOWED if is_provider_module(path) else CORE_ALLOWED
    unexpected = module_level_imports(ast.parse(path.read_text())) - allowed
    assert not unexpected, (
        f"{path.relative_to(CORE)} imports {sorted(unexpected)} at module scope.\n"
        "If agentloop genuinely needs it, add it to CORE_ALLOWED and say why in the "
        "commit message. If it touches the filesystem, a process or the network, it "
        "belongs in an application instead."
    )


@pytest.mark.parametrize("path", SOURCES, ids=IDS)
def test_core_never_reaches_up_or_out(path: Path) -> None:
    found = import_roots(ast.walk(ast.parse(path.read_text()))) & NEVER
    assert not found, (
        f"{path.relative_to(CORE)} imports {sorted(found)} somewhere in the file.\n"
        "Reaching the filesystem, a process or a socket is an application's job."
    )


def test_the_package_is_typed() -> None:
    assert (CORE / "py.typed").is_file()


# --- the fake ---


def test_the_fake_satisfies_the_protocol_with_no_base_class() -> None:
    client: LLMClient = FakeClient()
    assert FakeClient.__bases__ == (object,)
    assert client.name == "fake"


def test_the_fake_records_requests() -> None:
    response = ModelResponse(
        content=(TextPart(text="ok"),), stop_reason=StopReason.END_TURN,
        usage=Usage(), model="fake-1", provider="fake",
    )
    client = FakeClient(response)
    request = ModelRequest(messages=(), system="be brief")
    assert client.complete(request) is response
    assert client.requests[0].system == "be brief"


def test_the_fake_raises_queued_exceptions() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        FakeClient(RuntimeError("boom")).complete(ModelRequest(messages=()))


def test_the_fake_explains_exhaustion() -> None:
    with pytest.raises(AssertionError, match="exhausted"):
        FakeClient().complete(ModelRequest(messages=()))


def test_the_fake_declares_hosted_tools_only_when_asked() -> None:
    from agentloop.provider import supported_hosted_tools

    assert supported_hosted_tools(FakeClient()) == frozenset()
    assert supported_hosted_tools(FakeClient(hosted_tools=["web_search"])) == {"web_search"}


def test_pydantic_is_a_real_dependency_not_an_extra() -> None:
    assert importlib.util.find_spec("pydantic") is not None
