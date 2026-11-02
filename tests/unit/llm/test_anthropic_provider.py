"""The Anthropic provider against recorded-style API responses (no network).

The real SDK runs against an ``httpx2.MockTransport`` that serves server-sent-event streams
built from the API's documented event shapes, so the request body the provider sends is
checked exactly as it would go over the wire.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from codecoremorph.config.models import LlmConfig, ModelPrice, ProviderConfig
from codecoremorph.core.errors import (
    ContextOverflowError,
    InvalidLLMRequestError,
    ProviderAuthError,
    ProviderUnavailableError,
    RateLimitedError,
)
from codecoremorph.core.models import (
    CompletionRequest,
    Message,
    OpaqueBlock,
    StopReason,
    SystemBlock,
    TextBlock,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from codecoremorph.llm.anthropic import AnthropicProvider
from codecoremorph.llm.anthropic.provider import FALLBACK_BETA, THINKING_BINDING_BETA

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a file.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    },
)


def sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def message_start(model: str = "claude-opus-5-5", **usage: int) -> dict[str, Any]:
    base = {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}  # fmt: skip
    return {
        "type": "message_start",
        "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                    "content": [], "stop_reason": None, "stop_sequence": None,
                    "usage": base | usage},
    }  # fmt: skip


def block(
    index: int, content_block: dict[str, Any], *deltas: dict[str, Any]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "content_block_start", "index": index, "content_block": content_block}
    ]
    events += [{"type": "content_block_delta", "index": index, "delta": d} for d in deltas]
    events.append({"type": "content_block_stop", "index": index})
    return events


def message_end(stop_reason: str, output_tokens: int = 50, **extra: Any) -> list[dict[str, Any]]:
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None} | extra
    return [
        {"type": "message_delta", "delta": delta, "usage": {"output_tokens": output_tokens}},
        {"type": "message_stop"},
    ]


THINKING = block(
    0,
    {"type": "thinking", "thinking": "", "signature": ""},
    {"type": "thinking_delta", "thinking": "Read the header first."},
    {"type": "signature_delta", "signature": "sig-1"},
)
TOOL_USE = block(
    1,
    {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}},
    {"type": "input_json_delta", "partial_json": '{"path": "src/'},
    {"type": "input_json_delta", "partial_json": 'a.cpp"}'},
)


class Api:
    """Serves queued responses and records the requests."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.responses: list[httpx2.Response] = []

    def queue_stream(self, *events: dict[str, Any] | list[dict[str, Any]]) -> None:
        flat: list[dict[str, Any]] = []
        for e in events:
            flat += e if isinstance(e, list) else [e]
        self.responses.append(
            httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*flat))
        )

    def queue_json(self, status: int, body: dict[str, Any]) -> None:
        self.responses.append(httpx2.Response(status, json=body))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def body(self, n: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[n].content)


@pytest.fixture
def api() -> Api:
    return Api()


def make_provider(
    api: Api, *, fallback: bool = True, llm: LlmConfig | None = None
) -> AnthropicProvider:
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(api)),
    )
    return AnthropicProvider(
        ProviderConfig(max_retries=0), llm=llm, refusal_fallback=fallback, client=client
    )


def request(**kwargs: Any) -> CompletionRequest:
    defaults: dict[str, Any] = {
        "model": "claude-opus-5-5",
        "system": [SystemBlock(text="You migrate C++ code.", cache_breakpoint=True)],
        "messages": [Message.user("Unit context ...", cache_breakpoint=True)],
        "tools": [READ_FILE],
        "max_output_tokens": 64000,
        "provider_options": {"effort": "high"},
    }
    return CompletionRequest(**(defaults | kwargs))


def test_request_shape(api: Api) -> None:
    api.queue_stream(message_start(), *THINKING, *TOOL_USE, *message_end("tool_use"))
    make_provider(api).complete(request())
    sent = api.requests[0]
    body = api.body()
    assert sent.url.path == "/v1/messages"
    betas = sent.headers["anthropic-beta"].split(",")
    assert FALLBACK_BETA in betas
    assert THINKING_BINDING_BETA in betas
    assert body["stream"] is True
    assert body["fallbacks"] == "default"
    assert body["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "error"},
    }
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 64000
    assert body["tool_choice"] == {"type": "auto"}
    (tool,) = body["tools"]
    assert tool["strict"] is True
    assert tool["eager_input_streaming"] is True
    # caching: system breakpoint, unit-context breakpoint, automatic tail
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["cache_control"] == {"type": "ephemeral"}
    # no universal sampling knobs, ever
    assert not {"temperature", "top_p", "top_k"} & set(body)


def test_tool_calls_and_thinking_round_trip_unchanged(api: Api) -> None:
    api.queue_stream(message_start(), *THINKING, *TOOL_USE, *message_end("tool_use"))
    provider = make_provider(api)
    first = provider.complete(request())
    assert first.stop_reason is StopReason.TOOL_USE
    (call,) = first.tool_calls
    assert (call.id, call.name, call.arguments) == ("toolu_1", "read_file", {"path": "src/a.cpp"})
    thinking = first.content[0]
    assert isinstance(thinking, OpaqueBlock)
    assert thinking.kind == "thinking"
    assert thinking.text == "Read the header first."

    # The next turn echoes the assistant message verbatim, then the tool result.
    api.queue_stream(
        message_start(),
        *block(0, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Done."}),
        *message_end("end_turn"),
    )
    history = [
        Message.user("Unit context ...", cache_breakpoint=True),
        first.as_message(),
        Message(
            role="user",
            content=[ToolResult(call_id="toolu_1", name="read_file", content="int a;")],
        ),
    ]
    second = provider.complete(request(messages=history))
    assert second.text == "Done."
    assert second.stop_reason is StopReason.END
    echoed = api.body()["messages"][1]["content"]
    assert echoed[0] == {"type": "thinking", "thinking": "Read the header first.",
                         "signature": "sig-1"}  # fmt: skip
    assert echoed[1] == {"type": "tool_use", "id": "toolu_1", "name": "read_file",
                         "input": {"path": "src/a.cpp"}}  # fmt: skip
    assert api.body()["messages"][2]["content"][0]["type"] == "tool_result"


def test_max_tokens_and_refusal_are_results(api: Api) -> None:
    truncated = block(
        0,
        {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {}},
        {"type": "input_json_delta", "partial_json": '{"path": "src/a'},
    )
    api.queue_stream(message_start(), *truncated, *message_end("max_tokens"))
    result = make_provider(api).complete(request())
    assert result.stop_reason is StopReason.MAX_TOKENS  # the agent must not run this call

    api.queue_stream(
        message_start(),
        *message_end("refusal", output_tokens=0, stop_details={
            "type": "refusal", "category": "cyber", "explanation": None,
            "fallback_credit_token": None, "fallback_has_prefill_claim": False,
            "recommended_model": None,
        }),
    )  # fmt: skip
    refused = make_provider(api).complete(request())
    assert refused.stop_reason is StopReason.REFUSAL
    assert refused.refusal_category == "cyber"


def test_a_mid_output_fallback_drops_the_declined_partial(api: Api) -> None:
    llm = LlmConfig(providers={"anthropic": ProviderConfig(prices={
        "claude-opus-5-5": ModelPrice(input_usd_per_mtok=4, output_usd_per_mtok=20),
        "claude-opus-5": ModelPrice(input_usd_per_mtok=5, output_usd_per_mtok=25),
    })})  # fmt: skip
    events = [
        message_start(),
        *block(0, {"type": "thinking", "thinking": "", "signature": "x"}),
        *block(1, {"type": "text", "text": ""}, {"type": "text_delta", "text": "Partial "}),
        *block(2, {"type": "tool_use", "id": "toolu_x", "name": "read_file", "input": {}},
               {"type": "input_json_delta", "partial_json": "{}"}),
        *block(3, {"type": "fallback", "from": {"model": "claude-opus-5-5"},
                   "to": {"model": "claude-opus-5"}}),
        *block(4, {"type": "text", "text": ""}, {"type": "text_delta", "text": "answer"}),
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 10, "iterations": [
             {"type": "message", "model": "claude-opus-5-5", "input_tokens": 1000,
              "output_tokens": 100, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0},
             {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 1000,
              "output_tokens": 10, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0},
         ]}},
        {"type": "message_stop"},
    ]  # fmt: skip
    api.queue_stream(*events)
    result = make_provider(api, llm=llm).complete(request())
    kinds = [b.kind if isinstance(b, OpaqueBlock) else b.type for b in result.content]
    assert kinds == ["text", "fallback", "text"]  # no thinking or tool call before the switch
    assert result.text == "Partial \nanswer"
    assert result.fallback_used
    assert result.usage.input_tokens == 2000
    assert result.usage.output_tokens == 110
    expected = (1000 * 4 + 100 * 20 + 1000 * 5 + 10 * 25) / 1_000_000
    assert result.cost_usd == pytest.approx(expected)


def test_sampling_options_are_refused(api: Api) -> None:
    with pytest.raises(InvalidLLMRequestError, match="no sampling parameters"):
        make_provider(api).complete(request(provider_options={"temperature": 0.2}))
    assert api.requests == []


def test_fallback_can_be_turned_off(api: Api) -> None:
    api.queue_stream(message_start(), *message_end("end_turn"))
    make_provider(api, fallback=False).complete(request(tools=[]))
    assert "fallbacks" not in api.body()
    assert FALLBACK_BETA not in api.requests[0].headers.get("anthropic-beta", "")
    assert "tools" not in api.body()


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"type": "authentication_error", "message": "invalid x-api-key"}, ProviderAuthError),
        (429, {"type": "rate_limit_error", "message": "slow down"}, RateLimitedError),
        (529, {"type": "overloaded_error", "message": "busy"}, ProviderUnavailableError),
        (400, {"type": "invalid_request_error",
               "message": "prompt is too long: 1200000 tokens > 1000000 maximum"},
         ContextOverflowError),
        (400, {"type": "invalid_request_error", "message": "bad field"}, InvalidLLMRequestError),
    ],
)  # fmt: skip
def test_errors_map_to_the_taxonomy(
    api: Api, status: int, body: dict[str, Any], error: type[Exception]
) -> None:
    api.queue_json(status, {"type": "error", "error": body})
    with pytest.raises(error):
        make_provider(api).complete(request())


def test_capabilities_count_tokens_and_health(api: Api) -> None:
    api.queue_json(200, {
        "id": "claude-opus-5-5", "type": "model", "display_name": "Claude Opus 5.5",
        "created_at": "2026-10-01T00:00:00Z", "max_input_tokens": 1000000,
        "max_tokens": 128000, "capabilities": {"structured_outputs": {"supported": True}},
    })  # fmt: skip
    provider = make_provider(api)
    caps = provider.capabilities("claude-opus-5-5")
    assert (caps.context_window, caps.max_output_tokens, caps.source) == (1000000, 128000, "api")
    assert provider.capabilities("claude-opus-5-5") is caps  # cached
    assert len(api.requests) == 1

    api.queue_json(200, {"input_tokens": 4321})
    assert provider.count_tokens(request()) == 4321
    counted = api.body()
    assert api.requests[-1].url.path == "/v1/messages/count_tokens"
    assert "fallbacks" not in counted
    assert counted["thinking"] == {"type": "adaptive"}

    api.queue_json(401, {"type": "error", "error": {"type": "authentication_error",
                                                    "message": "no key"}})  # fmt: skip
    health = provider.health_check()
    assert not health.ok
    assert "credentials" in health.detail


def test_models_api_failure_falls_back_to_package_data(api: Api) -> None:
    api.queue_json(404, {"type": "error", "error": {"type": "not_found_error",
                                                    "message": "nope"}})  # fmt: skip
    caps = make_provider(api).capabilities("claude-opus-5-5")
    assert (caps.context_window, caps.source) == (1_000_000, "package")


def test_blocks_from_other_providers_are_not_sent(api: Api) -> None:
    api.queue_stream(message_start(), *message_end("end_turn"))
    history = [
        Message.user("hi"),
        Message(
            role="assistant",
            content=[
                OpaqueBlock(provider="ollama", kind="thinking", data={"t": "x"}),
                TextBlock(text="hello", provider="ollama"),
                ToolCall(id="call_0", name="read_file", arguments={"path": "a"}),
            ],
        ),
        Message(role="user", content=[ToolResult(call_id="call_0", name="read_file", content="x")]),
    ]
    make_provider(api).complete(request(messages=history))
    assert api.body()["messages"][1]["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "call_0", "name": "read_file", "input": {"path": "a"}},
    ]


class _NoCredentials:
    """Stands in for the SDK client when no credential source resolves: SDK 2.x raises this
    TypeError (not an AnthropicError) when a request is built."""

    message = (
        '"Could not resolve authentication method. Expected one of api_key, auth_token, or '
        'credentials to be set."'
    )

    def __getattr__(self, name: str) -> _NoCredentials:
        return self

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise TypeError(self.message)


def test_missing_credentials_are_an_auth_error() -> None:
    """Regression: the TypeError crashed `codecoremorph doctor`."""
    provider = AnthropicProvider(client=_NoCredentials())  # type: ignore[arg-type]
    health = provider.health_check()
    assert not health.ok
    assert "no Anthropic credentials" in health.detail
    with pytest.raises(ProviderAuthError, match="no Anthropic credentials"):
        provider.count_tokens(request())
    assert provider.capabilities("claude-opus-5-5").source == "package"
