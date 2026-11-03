"""The LangGraph wrapper (``orchestrator/graph.py``): long loops, interrupts and their answers."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.orchestrator.graph import STOP, NodeSpec, RunGraph, RunState, wait_for_decision


def _counter(limit: int) -> list[NodeSpec]:
    def step(state: RunState) -> RunState:
        return {"status": str(int(state.get("status") or "0") + 1)}

    def route(state: RunState) -> str:
        return STOP if int(state.get("status") or "0") >= limit else "step"

    return [NodeSpec("step", step, route, ("step", STOP))]


def test_a_graph_loops_far_beyond_langgraphs_default_limit(tmp_path: Path) -> None:
    """A run loops once per unit: hundreds of units must fit in one invoke (LangGraph's default
    limit of 25 steps would stop a run of about ten units)."""
    with RunGraph(_counter(400), "step", tmp_path / "graph.db", "t") as graph:
        outcome = graph.start({"status": "0"})
    assert outcome.waiting is None
    assert outcome.state.get("status") == "400"


def test_an_answer_is_kept_when_the_node_fails_after_it(tmp_path: Path) -> None:
    """LangGraph replays the first answer to an interrupt after the node failed and ignores a
    new one; the host-agent node relies on knowing this (it takes its action elsewhere)."""
    seen: list[object] = []
    failures = [RuntimeError("the sandbox is down")]

    def ask(state: RunState) -> RunState:
        seen.append(wait_for_decision({"gate": "agent"}))
        if failures:
            raise failures.pop()
        return {"status": "done"}

    nodes = [NodeSpec("ask", ask, STOP)]
    with RunGraph(nodes, "ask", tmp_path / "graph.db", "t") as graph:
        assert graph.start({}).waiting == {"gate": "agent"}
        with pytest.raises(RuntimeError):
            graph.resume("first")
        assert graph.pending() == {"gate": "agent"}
        outcome = graph.resume("second")
    assert seen == ["first", "first"]
    assert outcome.state.get("status") == "done"


def test_a_node_that_loops_to_itself_asks_again(tmp_path: Path) -> None:
    """The host-agent node waits again after a failed attempt by routing to itself: each
    visit is a new interrupt, and an answer the resumed visit didn't take is dropped."""
    visits = [0]

    def ask(state: RunState) -> RunState:
        visits[0] += 1
        if visits[0] == 2:  # the resumed visit returns early, without asking
            return {"status": "again"}
        answer = wait_for_decision({"gate": "agent", "visit": visits[0]})
        return {"status": str(answer)}

    def route(state: RunState) -> str:
        return "ask" if state.get("status") == "again" else STOP

    nodes = [NodeSpec("ask", ask, route, ("ask", STOP))]
    with RunGraph(nodes, "ask", tmp_path / "graph.db", "t") as graph:
        assert graph.start({}).waiting == {"gate": "agent", "visit": 1}
        assert graph.resume("for visit 1").waiting == {"gate": "agent", "visit": 3}
        outcome = graph.resume("for visit 3")
    assert outcome.waiting is None
    assert outcome.state.get("status") == "for visit 3"
