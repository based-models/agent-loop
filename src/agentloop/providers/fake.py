"""A scripted LLMClient, for tests.

Shipped in the package rather than in tests/, because anyone writing tests against
code that takes an LLMClient wants one. Zero dependencies: no HTTP, no mocking
library, no base class.
"""

from __future__ import annotations

from collections.abc import Iterable

from agentloop.provider import ModelRequest, ModelResponse


class FakeClient:
    """Replays scripted responses in order. Records every request it receives.

    A queued Exception is raised instead of returned, which is how retry and error
    paths get tested without patching a transport. `hosted_tools` is the optional
    capability attribute `supported_hosted_tools()` reads; empty by default.
    """

    name = "fake"

    def __init__(
        self,
        *responses: ModelResponse | Exception,
        model: str = "fake-1",
        hosted_tools: Iterable[str] = (),
    ) -> None:
        self.model = model
        self.hosted_tools = frozenset(hosted_tools)
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
            raise nxt
        return nxt
