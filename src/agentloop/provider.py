"""The provider seam.

Everything above this line is provider-agnostic; everything below it (the
`providers/` package) is not. The loop only ever calls `LLMClient.complete`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentloop.transcript import ContentPart, Message, Origin


class StopReason(StrEnum):
    """Why the model stopped, normalized across providers.

    Two rules make this trustworthy, and every adapter must apply both:
      1. Any ToolCallPart in the content forces TOOL_USE, whatever the wire said --
         EXCEPT MAX_TOKENS, which survives. A call cut off mid-arguments must reach
         the loop as truncated, so it is discarded rather than executed.
      2. Unrecognized values map to UNKNOWN, never to END_TURN.

    PAUSED is a provider-hosted tool loop stopping partway through a turn
    (Anthropic's `pause_turn`). The turn is unfinished: the loop sends it back
    as-is and the provider carries on.
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    REFUSAL = "refusal"
    ERROR = "error"
    PAUSED = "paused"
    UNKNOWN = "unknown"


type ToolChoice = Literal["auto", "none", "required"]


class ToolSchema(BaseModel):
    """One tool, as the model sees it.

    `parameters` is the BARE JSON Schema. Anthropic sends it as `input_schema`;
    OpenAI-compatible servers wrap it in a `function` envelope.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, Any]
    strict: bool = True


class HostedTool(BaseModel):
    """A tool the PROVIDER runs -- declared in the request, never dispatched here.

    Deliberately generic: `kind` names the capability ("web_search") and
    `options` are passed to the adapter, which translates or rejects them. The
    typed, validated constructors live with the tools (agentloop-tools), so this
    library learns one idea -- hosted tools -- and nothing about the web.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class ModelRequest(BaseModel):
    """Everything needed to make one model call, in canonical form."""

    model_config = ConfigDict(frozen=True)

    messages: tuple[Message, ...]
    system: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    max_tokens: int = Field(default=16_000, gt=0)
    # None means OMIT the parameter. Current Claude models 400 if it is present.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stop_sequences: tuple[str, ...] = ()
    tool_choice: ToolChoice = "auto"
    parallel_tool_calls: bool = True
    hosted_tools: tuple[HostedTool, ...] = ()


class Usage(BaseModel):
    """Token accounting. `None` means the provider did not report it."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    # Whether `input_tokens` already counts `cache_read_tokens`. OpenAI-style APIs
    # include them; Anthropic reports them separately. Only a hit-rate needs to know.
    input_includes_cached: bool = True


class ModelResponse(BaseModel):
    """One model turn, normalized.

    `content` must be non-empty: an adapter that would return an empty turn
    raises ValidationError instead, which it converts to ResponseInvalid.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    content: tuple[ContentPart, ...] = Field(min_length=1)
    stop_reason: StopReason
    usage: Usage
    model: str
    provider: str
    raw_stop_reason: str | None = None
    request_id: str | None = None
    # Provider attempts this response took, set by RetryingClient. 1 means no retry.
    attempts: int = Field(default=1, ge=1)
    # Seconds RetryingClient slept between those attempts. 0 means none.
    retry_wait_seconds: float = Field(default=0.0, ge=0)
    # The adapter's own wire form of this turn, for exact replay. See Origin.
    origin: Origin | None = None


class LLMClient(Protocol):
    """The seam. One method, no hidden state, no vendor types in the signature."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    # Reserved for streaming. Note: a SYNC iterator — streaming needs no asyncio.
    # def complete_stream(self, request: ModelRequest) -> Iterator[StreamEvent]: ...


def supported_hosted_tools(client: object) -> frozenset[str]:
    """The hosted tool kinds a client can run: its `hosted_tools`, if it has one.

    Optional on purpose. A client that says nothing supports nothing, so a test
    fake or a custom provider needs no new attribute to stay an LLMClient.
    """
    return frozenset(getattr(client, "hosted_tools", frozenset()))
