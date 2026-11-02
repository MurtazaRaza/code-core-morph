"""The Ollama provider against a mock Ollama server (no network)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import ollama
import pytest

from codecoremorph.core.errors import InvalidLLMRequestError, ProviderUnavailableError
from codecoremorph.core.models import (
    CompletionRequest,
    Message,
    StopReason,
    SystemBlock,
    ToolResult,
    ToolSpec,
)
from codecoremorph.llm.ollama import OllamaProvider
from codecoremorph.llm.ollama.provider import parse_text_tool_call

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


class Server:
    def __init__(self, *, tools: bool = True, context: int = 40960):
        self.show = {
            "model_info": {"general.architecture": "qwen3", "qwen3.context_length": context},
            "capabilities": ["completion", "tools"] if tools else ["completion"],
        }
        self.chats: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.models = ["qwen3:4b"]
        self.down = False

    def reply(self, content: str = "", *, calls: list[dict[str, Any]] | None = None,
              thinking: str | None = None, done_reason: str = "stop") -> None:  # fmt: skip
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = [{"function": c} for c in calls]
        if thinking:
            message["thinking"] = thinking
        self.replies.append(
            {
                "model": "qwen3:4b",
                "created_at": "2026-11-02T00:00:00Z",
                "message": message,
                "done": True,
                "done_reason": done_reason,
                "prompt_eval_count": 120,
                "eval_count": 30,
            }
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path == "/api/show":
            return httpx.Response(200, json=self.show)
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"model": m, "name": m} for m in self.models]}
            )
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            self.chats.append(body)
            if body["model"] not in self.models:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            return httpx.Response(200, json=self.replies.pop(0))
        return httpx.Response(404, json={"error": "unknown path"})


def make(server: Server) -> OllamaProvider:
    client = ollama.Client(host="http://ollama.test:11434", transport=httpx.MockTransport(server))
    return OllamaProvider(client=client)


def request(**kwargs: Any) -> CompletionRequest:
    defaults: dict[str, Any] = {
        "model": "qwen3:4b",
        "system": [SystemBlock(text="You migrate C++ code.")],
        "messages": [Message.user("Unit context")],
        "tools": [READ_FILE],
        "max_output_tokens": 4096,
        "provider_options": {"num_ctx": 32768},
    }
    return CompletionRequest(**(defaults | kwargs))


def test_native_tools_and_num_ctx() -> None:
    server = Server()
    server.reply(calls=[{"name": "read_file", "arguments": {"path": "src/a.cpp"}}],
                 thinking="Look first.")  # fmt: skip
    provider = make(server)
    result = provider.complete(request())
    sent = server.chats[0]
    assert sent["options"]["num_ctx"] == 32768  # always explicit
    assert sent["options"]["num_predict"] == 4096
    assert sent["messages"][0] == {"role": "system", "content": "You migrate C++ code."}
    assert sent["tools"][0]["function"]["name"] == "read_file"
    assert result.stop_reason is StopReason.TOOL_USE
    (call,) = result.tool_calls
    assert (call.name, call.arguments, call.id) == ("read_file", {"path": "src/a.cpp"}, "call_1_0")
    assert result.cost_usd == 0.0
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 30)

    # The tool result goes back as a tool message.
    server.reply("Done.")
    history = [
        Message.user("Unit context"),
        result.as_message(),
        Message(role="user", content=[ToolResult(call_id=call.id, name="read_file", content="x")]),
    ]
    final = provider.complete(request(messages=history))
    assert final.text == "Done."
    replayed = server.chats[1]["messages"]
    assert replayed[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert replayed[2]["thinking"] == "Look first."
    assert replayed[3] == {"role": "tool", "content": "x", "tool_name": "read_file"}


def test_num_ctx_defaults_to_the_models_window_and_warns_when_too_small(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = Server(context=40960)
    server.reply("ok")
    make(server).complete(request(provider_options={}))
    assert server.chats[0]["options"]["num_ctx"] == 40960
    server.reply("ok")
    make(server).complete(
        request(messages=[Message.user("x" * 10_000)], provider_options={"num_ctx": 512})
    )
    assert "truncates it silently" in caplog.text


def test_text_protocol_when_the_model_has_no_tools() -> None:
    server = Server(tools=False)
    server.reply(
        'I will read it.\n```json\n{"tool": "read_file", "arguments": {"path": "a.h"}}\n```'
    )
    result = make(server).complete(request())
    sent = server.chats[0]
    assert not sent.get("tools")
    assert "#### read_file" in sent["messages"][0]["content"]
    (call,) = result.tool_calls
    assert (call.name, call.arguments) == ("read_file", {"path": "a.h"})
    assert result.stop_reason is StopReason.TOOL_USE


def test_parse_text_tool_call() -> None:
    text = 'Thinking.\n```json\n{"tool": "x", "arguments": {"a": 1}}\n```\n'
    rest, call = parse_text_tool_call(text, "c1")
    assert rest == "Thinking."
    assert call is not None
    assert (call.name, call.arguments) == ("x", {"a": 1})
    assert parse_text_tool_call("no call here", "c1") == ("no call here", None)
    assert parse_text_tool_call("```json\n{not json}\n```", "c1")[1] is None


def test_length_stop_and_errors() -> None:
    server = Server()
    server.reply("partial", done_reason="length")
    assert make(server).complete(request(tools=[])).stop_reason is StopReason.MAX_TOKENS
    with pytest.raises(InvalidLLMRequestError, match="doesn't have the model"):
        make(server).complete(request(model="missing:1b", tools=[]))


def test_health() -> None:
    server = Server()
    provider = make(server)
    assert provider.health_check("qwen3:4b").ok
    missing = provider.health_check("llama9:70b")
    assert not missing.ok
    assert "ollama pull llama9:70b" in missing.detail
    server.down = True
    assert not provider.health_check().ok
    with pytest.raises(ProviderUnavailableError, match="isn't reachable"):
        OllamaProvider(
            client=ollama.Client(host="http://x:1", transport=httpx.MockTransport(server))
        ).complete(request())
