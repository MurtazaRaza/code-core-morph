"""Provider-neutral LLM contracts (SPEC §7.7, §9).

Conversations are append-only: an assistant message goes back to its provider exactly as it
came, so every block a provider returns keeps the provider's own JSON in ``raw``. Blocks with
no neutral meaning (thinking, refusal-fallback markers) are :class:`OpaqueBlock`s, which only
their own provider reads. There is deliberately no temperature or other sampling field:
provider-specific settings go in ``provider_options``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue

from codecoremorph.core.models.base import CoreModel

JsonObject = dict[str, JsonValue]


class TextBlock(CoreModel):
    type: Literal["text"] = "text"
    text: str
    provider: str | None = Field(default=None, description="Who produced it, if a provider did")
    raw: JsonObject | None = Field(default=None, description="The provider's block, verbatim")


class ToolCall(CoreModel):
    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: JsonObject = Field(default_factory=dict[str, JsonValue])
    provider: str | None = None
    raw: JsonObject | None = None


class ToolResult(CoreModel):
    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1, description="The tool's name (some providers need it)")
    content: str
    is_error: bool = False


class OpaqueBlock(CoreModel):
    """A provider-specific block (thinking, a refusal-fallback marker, ...), passed back to
    its provider unchanged and dropped for any other provider."""

    type: Literal["opaque"] = "opaque"
    provider: str
    kind: str = Field(description='The provider\'s block type, e.g. "thinking"')
    data: JsonObject
    text: str | None = Field(default=None, description="Readable content, for transcripts")


ContentBlock = Annotated[
    TextBlock | ToolCall | ToolResult | OpaqueBlock, Field(discriminator="type")
]


class Message(CoreModel):
    role: Literal["user", "assistant"]
    content: list[ContentBlock] = Field(min_length=1)
    cache_breakpoint: bool = Field(
        default=False, description="Cache the conversation up to the end of this message"
    )

    @classmethod
    def user(cls, text: str, *, cache_breakpoint: bool = False) -> Message:
        return cls(role="user", content=[TextBlock(text=text)], cache_breakpoint=cache_breakpoint)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [b for b in self.content if isinstance(b, ToolCall)]


class SystemBlock(CoreModel):
    text: str = Field(min_length=1)
    cache_breakpoint: bool = False


class ToolSpec(CoreModel):
    """A client tool. Schemas follow strict-mode rules: every object closed and every
    property required (optional values are nullable)."""

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(min_length=1)
    input_schema: JsonObject
    strict: bool = True


class CompletionRequest(CoreModel):
    model: str = Field(min_length=1)
    system: list[SystemBlock] = Field(default_factory=list[SystemBlock])
    messages: list[Message] = Field(min_length=1)
    tools: list[ToolSpec] = Field(default_factory=list[ToolSpec])
    response_schema: JsonObject | None = Field(
        default=None, description="Structured output for the final answer"
    )
    max_output_tokens: int = Field(ge=1)
    provider_options: JsonObject = Field(
        default_factory=dict[str, JsonValue],
        description='Provider-specific, e.g. {"effort": "high"} or {"num_ctx": 32768}',
    )


class StopReason(StrEnum):
    END = "end"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"


class Usage(CoreModel):
    input_tokens: int = Field(default=0, ge=0, description="Uncached input")
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class CompletionResult(CoreModel):
    provider: str
    model: str = Field(description="The model that produced the answer (after any fallback)")
    content: list[ContentBlock]
    stop_reason: StopReason
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float | None = Field(default=None, ge=0, description="None for free providers")
    latency_s: float = Field(default=0.0, ge=0)
    refusal_category: str | None = None
    fallback_used: bool = Field(
        default=False, description="A server-side refusal fallback model answered"
    )
    request_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [b for b in self.content if isinstance(b, ToolCall)]

    def as_message(self) -> Message:
        """The assistant turn to append to the conversation, unchanged."""
        content = self.content or [TextBlock(text="")]
        return Message(role="assistant", content=content)


class ModelCapabilities(CoreModel):
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    tools: bool = True
    structured_output: bool = True
    source: Literal["api", "package", "config", "default"] = "default"


class HealthStatus(CoreModel):
    ok: bool
    detail: str
