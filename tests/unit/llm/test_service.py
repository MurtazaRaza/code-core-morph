"""Replay, routing, the usage ledger and its caps (FR-3.7, FR-8.3 to FR-8.5, NFR-7.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.config.models import EscalationConfig, LlmConfig, RoleModelConfig
from codecoremorph.core.errors import BudgetExhaustedError, ReplayMissError
from codecoremorph.core.models import (
    CompletionRequest,
    CompletionResult,
    Message,
    Role,
    StopReason,
    TextBlock,
    Usage,
)
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import (
    Cassette,
    RecordingProvider,
    ReplayProvider,
    ScriptedProvider,
    request_key,
)
from codecoremorph.llm.routing import Router
from codecoremorph.llm.service import LLMService

OPUS = RoleModelConfig(provider="anthropic", model="claude-opus-5-5", effort="high")
LOCAL = RoleModelConfig(provider="ollama", model="qwen3:4b", num_ctx=32768)


def answer(text: str, *, cost: float | None = 0.01, tokens: int = 1000) -> CompletionResult:
    return CompletionResult(
        provider="anthropic",
        model="x",
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END,
        usage=Usage(input_tokens=tokens, output_tokens=0),
        cost_usd=cost,
    )


def ask(text: str, model: str = "claude-opus-5-5") -> CompletionRequest:
    return CompletionRequest(model=model, messages=[Message.user(text)], max_output_tokens=100)


def test_request_keys_ignore_line_endings_but_not_content() -> None:
    assert request_key(ask("a\r\nb")) == request_key(ask("a\nb"))
    assert request_key(ask("a\nb")) != request_key(ask("a\nc"))
    assert request_key(ask("a")) != request_key(ask("a"), "count_tokens")


def test_record_then_replay(tmp_path: Path) -> None:
    cassette = Cassette(tmp_path / "unit.json")
    recorder = RecordingProvider(ScriptedProvider([answer("one"), answer("two")]), cassette)
    assert recorder.complete(ask("q1")).text == "one"
    assert recorder.complete(ask("q1")).text == "two"  # the same request, twice
    assert recorder.count_tokens(ask("q2")) > 0

    replay = ReplayProvider(Cassette.load(tmp_path / "unit.json"), "anthropic")
    assert replay.complete(ask("q1\r\n".strip())).text == "one"
    assert replay.complete(ask("q1")).text == "two"
    assert replay.count_tokens(ask("q2")) == recorder.cassette.entries[2].tokens
    assert replay.unused == 0
    with pytest.raises(ReplayMissError, match="no response for this request") as miss:
        replay.complete(ask("q1"))
    assert miss.value.reason is not None
    assert "replayed 2 times" in miss.value.reason
    with pytest.raises(ReplayMissError) as miss:
        replay.complete(ask("something new"))
    assert miss.value.reason is not None
    assert "something new" in miss.value.reason


def test_scripted_provider_fails_loudly_when_exhausted() -> None:
    scripted = ScriptedProvider([lambda req: answer(req.messages[-1].text.upper())])
    assert scripted.complete(ask("hi")).text == "HI"
    with pytest.raises(ReplayMissError, match="ran out of responses"):
        scripted.complete(ask("again"))


def test_routing_fallback_and_escalation() -> None:
    llm = LlmConfig(
        roles={Role.EDITOR: OPUS, Role.SUMMARIZER: LOCAL},
        escalation=EscalationConfig(
            after_failed_attempts=2,
            to=RoleModelConfig(provider="anthropic", model="claude-fable-5-1", effort="xhigh"),
        ),
    )
    router = Router(llm)
    editor = router.route(Role.EDITOR)
    assert (editor.name, editor.options) == ("anthropic/claude-opus-5-5", {"effort": "high"})
    assert router.route(Role.FIXER).model == "claude-opus-5-5"  # unset: falls back to editor
    assert router.route(Role.FIXER, failed_attempts=1).escalated is False
    escalated = router.route(Role.FIXER, failed_attempts=2)
    assert (escalated.model, escalated.options, escalated.escalated) == (
        "claude-fable-5-1",
        {"effort": "xhigh"},
        True,
    )
    assert router.route(Role.EDITOR, failed_attempts=5).escalated is False
    assert router.route(Role.SUMMARIZER).options == {"num_ctx": 32768}
    assert router.providers() == ["anthropic", "ollama"]
    assert not router.local_only()
    assert Router(LlmConfig(roles={Role.EDITOR: LOCAL})).local_only()


def test_ledger_caps_hold_to_one_call_of_overshoot() -> None:
    ledger = UsageLedger(max_cost_usd=0.025)
    service = LLMService(
        LlmConfig(roles={Role.EDITOR: OPUS}),
        ledger=ledger,
        providers={"anthropic": ScriptedProvider([answer("a")] * 5)},
    )
    route = service.route(Role.EDITOR)
    for _ in range(3):  # 0.00 -> 0.01 -> 0.02 -> 0.03: the third call is the one overshoot
        service.complete(route, ask("x"), unit_id="U-0001", attempt=1)
    with pytest.raises(BudgetExhaustedError, match=r"cost cap of \$0\.03"):
        service.complete(route, ask("x"), unit_id="U-0001", attempt=1)
    assert ledger.total.cost_usd == pytest.approx(0.03)
    assert ledger.by_unit()["U-0001"].calls == 3
    assert ledger.by_role()[Role.EDITOR].usage.input_tokens == 3000
    assert ledger.providers_used() == ["anthropic"]

    tokens = UsageLedger(max_tokens=1500)
    tokens.check()
    service = LLMService(
        LlmConfig(roles={Role.EDITOR: OPUS}),
        ledger=tokens,
        providers={"anthropic": ScriptedProvider([answer("a", cost=None)] * 5)},
    )
    service.complete(route, ask("x"))
    service.complete(route, ask("x"))
    with pytest.raises(BudgetExhaustedError, match="token cap"):
        service.complete(route, ask("x"))


def test_capabilities_use_num_ctx_for_local_models() -> None:
    service = LLMService(
        LlmConfig(roles={Role.EDITOR: LOCAL}),
        providers={"ollama": ScriptedProvider([], name="ollama")},
    )
    caps = service.capabilities(service.route(Role.EDITOR))
    assert (caps.context_window, caps.source) == (32768, "config")
