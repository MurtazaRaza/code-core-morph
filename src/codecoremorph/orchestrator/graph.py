"""The only module that talks to LangGraph (SPEC §7.5: graph construction stays in
``orchestrator/`` to contain LangGraph API changes).

A graph here is a set of named nodes, each a plain function from the small run state to an
update of it, plus a routing function per node. Checkpoints go to a SQLite file after every
node (``durability="sync"``), so a killed engine resumes from the last finished node. Gates use
LangGraph's human-in-the-loop interrupt: a node calls :func:`wait_for_decision`, the graph
stops, and :meth:`RunGraph.resume` continues it with the decision.

LangSmith tracing is switched off for this process: CodeCoreMorph sends nothing anywhere but
the configured model providers (NFR-5.1), whatever the user's environment says.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, TypedDict, cast

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import (  # pyright: ignore[reportMissingTypeStubs]
    END,
    START,
    StateGraph,
)
from langgraph.types import Command, interrupt

from codecoremorph.core.errors import StoreError

for _name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ[_name] = "false"


class RunState(TypedDict, total=False):
    """The run graph's state: IDs, counters and statuses only (NFR-1.2). Everything large
    (plans, diffs, logs, transcripts) lives in the store and is found by ID."""

    status: str  # RunStatus value
    unit: str | None  # the unit being worked on, or None between units
    gate: bool  # the unit waits at its gate
    agent: bool  # the unit (or characterization job) waits for its host agent's edits (D-44)
    characterized: bool  # the characterization step is over (D-46)
    stop: str | None  # why the run stops early: aborted | budget_exhausted | rejected | failed
    reason: str | None  # one line for the report
    verified: bool | None  # the final verification passed


class Node(Protocol):
    def __call__(self, state: RunState) -> RunState: ...


Route = Callable[[RunState], str]
STOP = END
# The run graph loops once per unit (and once per host-agent attempt), so one invoke can take
# many steps. LangGraph's default limit, 25 steps per invoke, would stop a run of about ten
# units; the loops are bounded by the plan, so the limit only has to stay out of the way.
_RECURSION_LIMIT = 1_000_000


@dataclass(frozen=True)
class NodeSpec:
    name: str
    run: Node
    route: Route | str  # the next node's name, or a function choosing it
    targets: Sequence[str] = ()  # every name ``route`` may return (END is "__end__")


def wait_for_decision(request: Mapping[str, Any]) -> Any:
    """Inside a node: stop the graph until a decision arrives, then return it.

    The node runs again from its start when the graph resumes, so everything before this call
    must be safe to repeat. The first answer is kept: if the node fails after it, every later
    resume replays that answer and ignores a new one."""
    return interrupt(dict(request))


@dataclass(frozen=True)
class Outcome:
    state: RunState
    waiting: dict[str, Any] | None  # the request of the interrupt the graph stopped at


class RunGraph:
    """A compiled run graph with its SQLite checkpointer. One per engine process."""

    def __init__(self, nodes: Sequence[NodeSpec], first: str, checkpoints: Path, thread: str):
        self.thread = thread
        try:
            checkpoints.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(checkpoints, check_same_thread=False)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(
                f"can't open the run's checkpoints {checkpoints}", reason=str(exc)
            ) from exc
        builder = StateGraph(RunState)
        for spec in nodes:
            builder.add_node(spec.name, spec.run)  # pyright: ignore[reportUnknownMemberType]
        builder.add_edge(START, first)
        for spec in nodes:
            if isinstance(spec.route, str):
                builder.add_edge(spec.name, spec.route)
            else:
                builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
                    spec.name, spec.route, list(spec.targets)
                )
        self._graph = builder.compile(  # pyright: ignore[reportUnknownMemberType]
            checkpointer=SqliteSaver(self._conn)
        )

    @property
    def _config(self) -> Any:
        return {"configurable": {"thread_id": self.thread}, "recursion_limit": _RECURSION_LIMIT}

    def started(self) -> bool:
        """Whether the graph has checkpoints for this run."""
        snapshot = self._graph.get_state(self._config)
        return bool(snapshot.values) or bool(snapshot.next)

    def pending(self) -> dict[str, Any] | None:
        """The request of the interrupt the graph waits at, if any."""
        snapshot = self._graph.get_state(self._config)
        for item in snapshot.interrupts:
            return cast(dict[str, Any], item.value)
        return None

    def state(self) -> RunState:
        return cast(RunState, self._graph.get_state(self._config).values)

    def start(self, state: RunState) -> Outcome:
        return self._invoke(state)

    def proceed(self) -> Outcome:
        """Continue from the last checkpoint (after a crash or a stop)."""
        return self._invoke(None)

    def resume(self, decision: Any) -> Outcome:
        """Answer the interrupt the graph waits at."""
        return self._invoke(Command(resume=decision))

    def _invoke(self, payload: Any) -> Outcome:
        result = cast(
            dict[str, Any],
            self._graph.invoke(  # pyright: ignore[reportUnknownMemberType]
                payload, self._config, durability="sync"
            ),
        )
        interrupts = cast(list[Any], result.pop("__interrupt__", []))
        waiting = cast(dict[str, Any], interrupts[0].value) if interrupts else None
        return Outcome(state=cast(RunState, result), waiting=waiting)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
