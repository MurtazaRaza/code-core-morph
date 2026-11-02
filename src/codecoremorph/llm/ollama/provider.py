"""Local models through the official ``ollama`` client (SPEC §7.7).

- ``num_ctx`` is always sent: Ollama's default context is small and silently truncates
  prompts. It comes from the role's ``num_ctx`` (the router puts it in ``provider_options``)
  or the model's maximum; a pre-flight check warns when the prompt looks larger.
- Native tool calling when the model reports the ``tools`` capability; otherwise a text
  protocol (``prompts/v1/text_tool_protocol.md``): the model replies with one fenced JSON
  call, which is parsed into the same :class:`ToolCall` the agent validates.
- Other ``provider_options`` (``temperature``, ``seed``, ...) go to Ollama's ``options``
  unchanged; ``think`` and ``keep_alive`` are request fields.
- Cost is zero, and no code leaves the machine (FR-8.5).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, ClassVar, Self, cast

import httpx
import ollama

from codecoremorph.config.models import LlmConfig, ProviderConfig
from codecoremorph.core.errors import (
    InvalidLLMRequestError,
    LLMError,
    LLMTimeoutError,
    ProviderUnavailableError,
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
    ToolSpec,
    Usage,
)
from codecoremorph.core.models.llm import JsonObject
from codecoremorph.prompts import render

log = logging.getLogger(__name__)

PROVIDER_NAME = "ollama"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_CONTEXT = 8192
_CHARS_PER_TOKEN = 3.0
_REQUEST_FIELDS = frozenset({"num_ctx", "think", "keep_alive", "text_tool_protocol"})
_FENCED_JSON = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)


def _estimate_tokens(request: CompletionRequest) -> int:
    chars = sum(len(b.text) for b in request.system)
    for message in request.messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                chars += len(block.text)
            elif isinstance(block, ToolResult):
                chars += len(block.content)
            elif isinstance(block, ToolCall):
                chars += len(json.dumps(block.arguments))
    chars += sum(len(json.dumps(t.input_schema)) + len(t.description) for t in request.tools)
    return int(chars / _CHARS_PER_TOKEN)


def describe_tools(tools: list[ToolSpec]) -> str:
    return "\n\n".join(
        f"#### {t.name}\n\n{t.description}\n\nJSON Schema:\n\n```json\n"
        f"{json.dumps(t.input_schema, indent=2, sort_keys=True)}\n```"
        for t in tools
    )


def parse_text_tool_call(text: str, call_id: str) -> tuple[str, ToolCall | None]:
    """The last fenced JSON block naming a tool, as a call; the rest stays text."""
    for match in reversed(list(_FENCED_JSON.finditer(text))):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            payload = cast(dict[str, Any], data)
            arguments = payload.get("arguments")
            call = ToolCall(
                id=call_id,
                name=str(payload["tool"]),
                arguments=cast(JsonObject, arguments) if isinstance(arguments, dict) else {},
                provider=PROVIDER_NAME,
            )
            return (text[: match.start()] + text[match.end() :]).strip(), call
    return text, None


class OllamaProvider:
    api_version: ClassVar[str] = "1"
    name: ClassVar[str] = PROVIDER_NAME
    local: ClassVar[bool] = True

    def __init__(
        self, config: ProviderConfig | None = None, *, client: ollama.Client | None = None
    ):
        self._config = config or ProviderConfig()
        self.host = self._config.base_url or DEFAULT_BASE_URL
        self._client = client or ollama.Client(
            host=self.host, timeout=self._config.timeout_s or 600
        )
        self._capabilities: dict[str, ModelCapabilities] = {}

    @classmethod
    def from_config(cls, config: ProviderConfig | None, llm: LlmConfig) -> Self:
        return cls(config)

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self._client.close()

    # ------------------------------------------------------------------ discovery

    def capabilities(self, model: str) -> ModelCapabilities:
        """From Ollama's model info; the role's ``num_ctx`` overrides the window."""
        if model in self._capabilities:
            return self._capabilities[model]
        try:
            shown = self._client.show(model)
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as exc:
            raise self._map_error(exc, model) from exc
        info = cast(dict[str, Any], shown.modelinfo or {})
        context = next(
            (int(v) for k, v in info.items() if k.endswith(".context_length") and v), None
        )
        tools = "tools" in (shown.capabilities or [])
        found = ModelCapabilities(
            context_window=context, tools=tools, structured_output=True, source="api"
        )
        self._capabilities[model] = found
        return found

    def health_check(self, model: str | None = None) -> HealthStatus:
        """Is the server reachable, and is ``model`` pulled?"""
        try:
            listed = self._client.list()
        except (ollama.ResponseError, httpx.HTTPError, ConnectionError) as exc:
            return HealthStatus(ok=False, detail=f"Ollama isn't reachable at {self.host}: {exc}")
        names = {m.model for m in listed.models if m.model}
        if model is None:
            return HealthStatus(ok=True, detail=f"Ollama at {self.host}: {len(names)} models")
        candidates = {model, f"{model}:latest"}
        if names & candidates:
            return HealthStatus(ok=True, detail=f"{model} is pulled")
        return HealthStatus(ok=False, detail=f"{model} isn't pulled; run: ollama pull {model}")

    # ------------------------------------------------------------------ requests

    def count_tokens(self, request: CompletionRequest) -> int:
        """Ollama has no counting endpoint: a character-based estimate."""
        return _estimate_tokens(request)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        options = dict(request.provider_options)
        caps = self.capabilities(request.model)
        num_ctx = options.get("num_ctx")
        if not isinstance(num_ctx, int):
            num_ctx = caps.context_window or DEFAULT_CONTEXT
        estimate = _estimate_tokens(request)
        if estimate > num_ctx:
            log.warning(
                "the prompt for %s is about %d tokens but num_ctx is %d; Ollama truncates "
                "it silently. Raise num_ctx for the role, or use a model with a larger window",
                request.model,
                estimate,
                num_ctx,
            )
        native_tools = caps.tools and options.get("text_tool_protocol") is not True
        system = "\n\n".join(b.text for b in request.system)
        if request.tools and not native_tools:
            protocol = render("text_tool_protocol", tools=describe_tools(request.tools))
            system = f"{system}\n\n{protocol}" if system else protocol
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for message in request.messages:
            messages += _message_params(message, native_tools=native_tools)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": num_ctx,
                "num_predict": request.max_output_tokens,
                **{k: v for k, v in options.items() if k not in _REQUEST_FIELDS},
            },
        }
        if request.tools and native_tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        if request.response_schema is not None:
            kwargs["format"] = request.response_schema
        for key in ("think", "keep_alive"):
            if key in options:
                kwargs[key] = options[key]
        started = time.monotonic()
        try:
            response = self._client.chat(**kwargs)
        except (ollama.ResponseError, ollama.RequestError, httpx.HTTPError, ConnectionError) as exc:
            raise self._map_error(exc, request.model) from exc
        return self._result(response, request, native_tools, time.monotonic() - started)

    def _result(
        self, response: Any, request: CompletionRequest, native: bool, latency_s: float
    ) -> CompletionResult:
        message = response.message
        content: list[ContentBlock] = []
        if message.thinking:
            content.append(
                OpaqueBlock(
                    provider=PROVIDER_NAME,
                    kind="thinking",
                    data={"thinking": message.thinking},
                    text=message.thinking,
                )
            )
        text = message.content or ""
        turn = len(request.messages)
        calls: list[ToolCall] = []
        if native:
            for i, call in enumerate(message.tool_calls or []):
                calls.append(
                    ToolCall(
                        id=f"call_{turn}_{i}",
                        name=call.function.name,
                        arguments=cast(JsonObject, dict(call.function.arguments or {})),
                        provider=PROVIDER_NAME,
                    )
                )
        elif request.tools:
            text, parsed = parse_text_tool_call(text, f"call_{turn}_0")
            if parsed is not None:
                calls.append(parsed)
                # Keep the model's own reply verbatim, so the conversation replays as it was.
                text = message.content or ""
        if text:
            content.append(TextBlock(text=text, provider=PROVIDER_NAME))
        content += calls
        if calls:
            stop = StopReason.TOOL_USE
        elif response.done_reason == "length":
            stop = StopReason.MAX_TOKENS
        else:
            stop = StopReason.END
        return CompletionResult(
            provider=PROVIDER_NAME,
            model=request.model,
            content=content,
            stop_reason=stop,
            usage=Usage(
                input_tokens=int(response.prompt_eval_count or 0),
                output_tokens=int(response.eval_count or 0),
            ),
            cost_usd=0.0,
            latency_s=round(latency_s, 3),
        )

    def _map_error(self, exc: Exception, model: str) -> LLMError:
        if isinstance(exc, ollama.ResponseError):
            if exc.status_code == 404:
                return InvalidLLMRequestError(
                    f"Ollama doesn't have the model {model}",
                    reason=str(exc.error),
                    hint=f"Pull it: scripts/pull-models {model}",
                )
            if exc.status_code >= 500:
                return ProviderUnavailableError("the Ollama server failed", reason=str(exc.error))
            return InvalidLLMRequestError("Ollama rejected the request", reason=str(exc.error))
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(f"the Ollama request to {self.host} timed out")
        if isinstance(exc, httpx.HTTPError | ConnectionError):
            return ProviderUnavailableError(
                f"Ollama isn't reachable at {self.host}",
                reason=str(exc),
                hint="Start Ollama (the app, or `ollama serve`), or set "
                "llm.providers.ollama.base_url.",
            )
        return InvalidLLMRequestError("the Ollama request is invalid", reason=str(exc))


def _message_params(message: Message, *, native_tools: bool) -> list[dict[str, Any]]:
    if message.role == "assistant":
        text = "\n".join(b.text for b in message.content if isinstance(b, TextBlock))
        param: dict[str, Any] = {"role": "assistant", "content": text}
        thinking = [
            b.text
            for b in message.content
            if isinstance(b, OpaqueBlock) and b.provider == PROVIDER_NAME and b.text
        ]
        if thinking:
            param["thinking"] = "\n".join(thinking)
        calls = message.tool_calls
        if calls and native_tools:
            param["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        elif calls and not text:
            # Another provider's tool calls, replayed through the text protocol.
            param["content"] = "\n".join(
                "```json\n" + json.dumps({"tool": c.name, "arguments": c.arguments}) + "\n```"
                for c in calls
            )
        return [param]
    params: list[dict[str, Any]] = []
    texts: list[str] = []
    for block in message.content:
        if isinstance(block, ToolResult):
            body = ("Error: " if block.is_error else "") + block.content
            if native_tools:
                params.append({"role": "tool", "content": body, "tool_name": block.name})
            else:
                texts.append(f"Tool result ({block.name}):\n{body}")
        elif isinstance(block, TextBlock):
            texts.append(block.text)
    if texts:
        params.append({"role": "user", "content": "\n\n".join(texts)})
    return params
