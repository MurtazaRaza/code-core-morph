"""Claude through the official ``anthropic`` SDK (SPEC §7.7).

Decisions this provider implements (written against SDK 2.x, API version 2026-10-21):

- Adaptive thinking is always on for current models; depth is set with an explicit
  ``output_config.effort`` (the API default for claude-opus-5-5 is ``medium``; the manifest's
  default is ``high``). No sampling parameters are ever sent.
- Tools are ``strict`` and, because every call streams, use ``eager_input_streaming`` (not with
  a custom ``base_url``, which may be a proxy that rejects the field). ``tool_choice`` stays
  ``auto``: forced tool use is rejected by current models.
- Server-side refusal fallback (``fallbacks: "default"``) is on unless the manifest turns it
  off. After a mid-output fallback, the declined model's thinking and tool calls before the
  last ``fallback`` block are dropped, as the API requires when the turn is echoed back.
- Conversations are append-only, so thinking blocks stay valid; the history-edit check is set
  to ``error`` so an accidental edit fails loudly instead of silently losing reasoning.
- Prompt caching: explicit breakpoints where the request marks them (after the system prompt
  and after the unit context) plus automatic caching for the growing conversation tail.
- Credentials come only from the SDK's default chain (``ANTHROPIC_API_KEY``,
  ``ANTHROPIC_AUTH_TOKEN``, an ``ant auth login`` profile, ...).
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar, Self, cast

import anthropic

from codecoremorph.config.models import LlmConfig, ProviderConfig
from codecoremorph.core.errors import (
    ContextOverflowError,
    InvalidLLMRequestError,
    LLMError,
    LLMTimeoutError,
    ProviderAuthError,
    ProviderUnavailableError,
    RateLimitedError,
)
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    ContentBlock,
    HealthStatus,
    Message,
    ModelCapabilities,
    OpaqueBlock,
    StopReason,
    TextBlock,
    ToolCall,
    ToolResult,
    Usage,
)
from codecoremorph.core.models.llm import JsonObject
from codecoremorph.llm.base import SAMPLING_OPTIONS
from codecoremorph.llm.catalog import cost_usd, package_capabilities, prices_for

log = logging.getLogger(__name__)

PROVIDER_NAME = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_THINKING_DISPLAY = "summarized"  # readable reasoning summaries for transcripts
FALLBACK_BETA = "server-side-fallback-2026-07-01"
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
_JSON_RETRIES = 2  # re-issues after the SDK can't parse a streamed tool input at all

_STOP_REASONS = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "pause_turn": StopReason.END,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}
# Blocks a declined model wrote before a fallback, which the turn must not echo back.
_DROP_BEFORE_FALLBACK = frozenset({"thinking", "redacted_thinking", "tool_use", "server_tool_use"})
_CONTEXT_MESSAGES = ("prompt is too long", "context window", "too many tokens")


def _option(options: JsonObject, key: str, default: str | None) -> str | None:
    value = options.get(key, default)
    return value if isinstance(value, str) else default


class AnthropicProvider:
    api_version: ClassVar[str] = "1"
    name: ClassVar[str] = PROVIDER_NAME
    local: ClassVar[bool] = False

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        llm: LlmConfig | None = None,
        refusal_fallback: bool = True,
        client: anthropic.Anthropic | None = None,
    ):
        self._config = config or ProviderConfig()
        self._llm = llm or LlmConfig()
        self._refusal_fallback = refusal_fallback
        self._eager_streaming = self._config.base_url is None
        self._client_instance = client
        self._capabilities: dict[str, ModelCapabilities] = {}

    @classmethod
    def from_config(cls, config: ProviderConfig | None, llm: LlmConfig) -> Self:
        return cls(config, llm=llm, refusal_fallback=llm.anthropic_refusal_fallback)

    def close(self) -> None:
        """Release the HTTP connection pool."""
        if self._client_instance is not None:
            self._client_instance.close()

    @property
    def _client(self) -> anthropic.Anthropic:
        if self._client_instance is None:
            kwargs: dict[str, Any] = {}
            if self._config.max_retries is not None:
                kwargs["max_retries"] = self._config.max_retries
            if self._config.timeout_s is not None:
                kwargs["timeout"] = self._config.timeout_s
            if self._config.base_url is not None:
                kwargs["base_url"] = self._config.base_url
            try:
                self._client_instance = anthropic.Anthropic(**kwargs)
            except anthropic.AnthropicError as exc:
                raise _map_error(exc) from exc
        return self._client_instance

    # ------------------------------------------------------------------ requests

    def _params(self, request: CompletionRequest) -> dict[str, Any]:
        options = request.provider_options
        sampling = sorted(SAMPLING_OPTIONS & set(options))
        if sampling:
            raise InvalidLLMRequestError(
                f"Anthropic models take no sampling parameters ({', '.join(sampling)})",
                hint="Remove them from the role's provider_options; use effort instead.",
            )
        thinking: dict[str, Any] = {
            "type": "adaptive",
            "display": _option(options, "thinking_display", DEFAULT_THINKING_DISPLAY),
        }
        betas: list[str] = []
        binding = _option(options, "prefix_mismatch_behavior", "error")
        if binding is not None:
            thinking["block_binding"] = {"prefix_mismatch_behavior": binding}
            betas.append(THINKING_BINDING_BETA)
        output_config: dict[str, Any] = {"effort": _option(options, "effort", DEFAULT_EFFORT)}
        if request.response_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.response_schema}
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": [_message_param(m) for m in request.messages],
            "thinking": thinking,
            "output_config": output_config,
            # Automatic caching of the conversation tail; explicit breakpoints mark the rest.
            "cache_control": {"type": "ephemeral"},
        }
        if request.system:
            params["system"] = [
                {"type": "text", "text": b.text}
                | ({"cache_control": {"type": "ephemeral"}} if b.cache_breakpoint else {})
                for b in request.system
            ]
        if request.tools:
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "strict": t.strict,
                }
                | ({"eager_input_streaming": True} if self._eager_streaming else {})
                for t in request.tools
            ]
            params["tool_choice"] = {"type": "auto"}
        if self._refusal_fallback:
            betas.append(FALLBACK_BETA)
            params["fallbacks"] = "default"
        if betas:
            params["betas"] = betas
        return params

    def complete(self, request: CompletionRequest) -> CompletionResult:
        params = self._params(request)
        started = time.monotonic()
        message: Any = None
        for attempt in range(_JSON_RETRIES + 1):
            try:
                with self._client.beta.messages.stream(**params) as stream:
                    for _ in stream:
                        pass
                    message = stream.get_final_message()
                break
            except anthropic.AnthropicError as exc:
                raise _map_error(exc) from exc
            except TypeError as exc:
                raise _credentials_error(exc) from exc
            except ValueError as exc:
                # A streamed tool input the SDK couldn't parse at all: there is no tool-use
                # block to answer, so the turn is re-issued (bounded).
                if attempt == _JSON_RETRIES:
                    raise InvalidLLMRequestError(
                        "Claude's tool input couldn't be parsed, repeatedly",
                        reason=str(exc),
                    ) from exc
                log.warning("unparseable streamed tool input; re-issuing the turn (%s)", exc)
        return self._result(message, time.monotonic() - started)

    def _result(self, message: Any, latency_s: float) -> CompletionResult:
        blocks = [cast(JsonObject, b.to_dict()) for b in message.content]
        content = [_neutral_block(b) for b in _without_declined_partial(blocks)]
        usage, cost, fallback_used = self._usage(message)
        stop = _STOP_REASONS.get(str(message.stop_reason), StopReason.END)
        details = message.stop_details
        category = getattr(details, "category", None) if details is not None else None
        return CompletionResult(
            provider=PROVIDER_NAME,
            model=str(message.model),
            content=content,
            stop_reason=stop,
            usage=usage,
            cost_usd=cost,
            latency_s=round(latency_s, 3),
            refusal_category=None if category is None else str(category),
            fallback_used=fallback_used,
            request_id=getattr(message, "_request_id", None),
        )

    def _usage(self, message: Any) -> tuple[Usage, float | None, bool]:
        """Per-attempt usage when a fallback ran (``usage.iterations`` is the source of
        truth then; each attempt bills at its own model's prices)."""
        top = message.usage
        iterations = list(getattr(top, "iterations", None) or [])
        attempts = iterations or [top]
        total = Usage()
        cost: float | None = 0.0
        fallback_used = False
        for item in attempts:
            kind = getattr(item, "type", "message")
            if kind not in ("message", "fallback_message"):
                continue  # compaction or advisor iterations: not ours
            fallback_used = fallback_used or kind == "fallback_message"
            part = Usage(
                input_tokens=int(item.input_tokens or 0),
                output_tokens=int(item.output_tokens or 0),
                cache_read_tokens=int(item.cache_read_input_tokens or 0),
                cache_write_tokens=int(item.cache_creation_input_tokens or 0),
            )
            total = total + part
            model = str(getattr(item, "model", None) or message.model)
            part_cost = cost_usd(
                prices_for(PROVIDER_NAME, model, self._llm),
                input_tokens=part.input_tokens,
                output_tokens=part.output_tokens,
                cache_read_tokens=part.cache_read_tokens,
                cache_write_tokens=part.cache_write_tokens,
            )
            cost = None if cost is None or part_cost is None else cost + part_cost
        return total, None if cost is None else round(cost, 6), fallback_used

    def count_tokens(self, request: CompletionRequest) -> int:
        params = self._params(request)
        kwargs: dict[str, Any] = {
            "model": params["model"],
            "messages": params["messages"],
            "thinking": {"type": "adaptive"},
        }
        for key in ("system", "tools", "tool_choice"):
            if key in params:
                kwargs[key] = params[key]
        try:
            counted = self._client.beta.messages.count_tokens(**kwargs)
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        except TypeError as exc:
            raise _credentials_error(exc) from exc
        return int(counted.input_tokens)

    # ------------------------------------------------------------------ discovery

    def capabilities(self, model: str) -> ModelCapabilities:
        """From the Models API, else package data (FR-8.2)."""
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            info = self._client.models.retrieve(model)
            raw = cast(dict[str, Any], info.to_dict())
            caps = cast(dict[str, Any], raw.get("capabilities") or {})
            structured = bool(caps.get("structured_outputs", {}).get("supported", True))
            found = ModelCapabilities(
                context_window=info.max_input_tokens or None,
                max_output_tokens=info.max_tokens or None,
                tools=True,
                structured_output=structured,
                source="api",
            )
        except (anthropic.AnthropicError, LLMError, TypeError) as exc:
            if isinstance(exc, TypeError) and not _missing_credentials(exc):
                raise
            log.debug("Models API lookup of %s failed: %s", model, exc)
            known = package_capabilities(PROVIDER_NAME, model)
            found = (
                ModelCapabilities(
                    context_window=known[0], max_output_tokens=known[1], source="package"
                )
                if known is not None
                else ModelCapabilities()
            )
        self._capabilities[model] = found
        return found

    def health_check(self, model: str | None = None) -> HealthStatus:
        """A cheap Models API call: do credentials resolve, and is the model available?"""
        target = model or DEFAULT_MODEL
        try:
            info = self._client.models.retrieve(target)
        except anthropic.AnthropicError as exc:
            error = _map_error(exc)
            return HealthStatus(ok=False, detail=f"{error.message}: {error.reason or exc}")
        except LLMError as exc:
            return HealthStatus(ok=False, detail=exc.message)
        except TypeError as exc:
            error = _credentials_error(exc)
            return HealthStatus(ok=False, detail=f"{error.message}; {error.hint}")
        return HealthStatus(ok=True, detail=f"{info.id} is available")


# ---------------------------------------------------------------------- conversion


def _message_param(message: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in message.content:
        param = _block_param(block)
        if param is not None:
            content.append(param)
    if not content:
        content = [{"type": "text", "text": "(empty)"}]
    if message.cache_breakpoint:
        for param in reversed(content):
            if param.get("type") in ("text", "tool_use", "tool_result"):
                param["cache_control"] = {"type": "ephemeral"}
                break
    return {"role": message.role, "content": content}


def _block_param(block: ContentBlock) -> dict[str, Any] | None:
    if isinstance(block, ToolResult):
        return {
            "type": "tool_result",
            "tool_use_id": block.call_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, OpaqueBlock):
        return dict(block.data) if block.provider == PROVIDER_NAME else None
    if block.provider == PROVIDER_NAME and block.raw is not None:
        return dict(block.raw)  # verbatim: append-only conversations
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text} if block.text else None
    return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.arguments}


def _without_declined_partial(blocks: list[JsonObject]) -> list[JsonObject]:
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    return [
        b for i, b in enumerate(blocks) if i > last or b.get("type") not in _DROP_BEFORE_FALLBACK
    ]


def _neutral_block(raw: JsonObject) -> ContentBlock:
    kind = str(raw.get("type"))
    if kind == "text":
        return TextBlock(text=str(raw.get("text", "")), provider=PROVIDER_NAME, raw=raw)
    if kind == "tool_use":
        arguments = raw.get("input")
        return ToolCall(
            id=str(raw.get("id")),
            name=str(raw.get("name")),
            arguments=cast(JsonObject, arguments) if isinstance(arguments, dict) else {},
            provider=PROVIDER_NAME,
            raw=raw,
        )
    text = raw.get("thinking")
    return OpaqueBlock(
        provider=PROVIDER_NAME, kind=kind, data=raw, text=text if isinstance(text, str) else None
    )


def _missing_credentials(exc: TypeError) -> bool:
    # SDK 2.x raises a TypeError, not an AnthropicError, when no credential source resolves.
    return "authentication method" in str(exc)


def _credentials_error(exc: TypeError) -> LLMError:
    if not _missing_credentials(exc):
        raise exc
    return ProviderAuthError(
        "no Anthropic credentials were found",
        reason=str(exc),
        hint="Set ANTHROPIC_API_KEY, or run `ant auth login`. CodeCoreMorph uses the "
        "Anthropic SDK's default credential chain.",
    )


def _map_error(exc: anthropic.AnthropicError) -> LLMError:
    """SDK exceptions → the provider-neutral taxonomy, most specific first."""
    message = str(getattr(exc, "message", None) or exc)
    if isinstance(exc, anthropic.CredentialsError | anthropic.AuthenticationError):
        return ProviderAuthError(
            "Anthropic credentials are missing or invalid",
            reason=message,
            hint="Set ANTHROPIC_API_KEY, or run `ant auth login`. CodeCoreMorph uses the "
            "Anthropic SDK's default credential chain.",
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderAuthError(
            "the Anthropic credentials aren't allowed to make this request",
            reason=message,
            hint="Check the key's workspace and the model's availability for your account.",
        )
    if isinstance(exc, anthropic.RateLimitError):
        return RateLimitedError("Anthropic rate limit reached", reason=message)
    if isinstance(exc, anthropic.RequestTooLargeError):
        return ContextOverflowError("the request is too large for the Anthropic API")
    if isinstance(exc, anthropic.NotFoundError):
        return InvalidLLMRequestError(
            "Anthropic doesn't know the requested model or endpoint",
            reason=message,
            hint="Check the model names under llm.roles.",
        )
    if isinstance(exc, anthropic.BadRequestError | anthropic.UnprocessableEntityError):
        if any(marker in message.lower() for marker in _CONTEXT_MESSAGES):
            return ContextOverflowError("the prompt doesn't fit the model's context window")
        return InvalidLLMRequestError("Anthropic rejected the request", reason=message)
    if isinstance(exc, anthropic.APITimeoutError | anthropic.DeadlineExceededError):
        return LLMTimeoutError("the Anthropic request timed out", reason=message)
    if isinstance(
        exc,
        anthropic.OverloadedError
        | anthropic.ServiceUnavailableError
        | anthropic.InternalServerError
        | anthropic.APIConnectionError,
    ):
        return ProviderUnavailableError("the Anthropic API is unavailable", reason=message)
    return LLMError(f"Anthropic API error ({type(exc).__name__})", reason=message)
