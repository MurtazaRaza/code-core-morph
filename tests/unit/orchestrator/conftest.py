"""A run engine over a temporary repository with the fake adapter (no toolchain): the fake
build fails on BROKEN, the tests fail on REGRESS, and the codemod rewrites simple typedefs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.config import language_config
from codecoremorph.config import load_config
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRecord,
    CompletionResult,
    FileRole,
    IndexMeta,
    SymbolIndex,
)
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Responder, ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.lifecycle import _end_session  # pyright: ignore[reportPrivateUsage]
from codecoremorph.orchestrator.run import (
    AgentAction,
    RunEngine,
    RunOptions,
    RunResult,
    new_run_record,
    run_environment,
)
from codecoremorph.orchestrator.session import RunSession
from codecoremorph.rules.registry import RuleRegistry
from codecoremorph.store import StateDir, Store
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore
from codecoremorph.vcs.workspace import RunWorkspace

from ..agent.conftest import TYPEDEFS, FakeAdapter, PathOnlyExecutor

GitRunner = Callable[..., str]
MACRO = "#define DECLARE(T, N) typedef T N"
FIXED = "#define DECLARE(T, N) using N = T"

_MANIFEST = """\
version: "1.0"
project:
  name: geo
  source_dirs: [src, include]
  test_dirs: [tests]
migration:
  source: {{language: cpp, standard: cpp11, build_system: cmake}}
  target: {{language: cpp, standard: cpp11, rules: [replace_typedefs_with_using]}}
parity: {parity}
approvals: {{plan: {plan}, unit: {unit}}}
guardrails: {{max_retry_budget: 1, max_edit_retries: 1{extra}}}
"""


class IndexingAdapter(FakeAdapter):
    """The fake adapter, with an index: every file with its role, and its includes."""

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        self.calls.append("index")
        path = ctx.cache_dir / INDEX_FILE_NAME
        rows: list[tuple[str, str, str, str, int, int, str, str]] = []
        deps: list[tuple[str, str, str]] = []
        for rel, text in sorted(self._files(ctx).items()):
            if rel.endswith(".txt"):
                role = FileRole.BUILD
            elif rel.startswith("tests/"):
                role = FileRole.TEST
            elif rel.endswith(".h"):
                role = FileRole.HEADER
            else:
                role = FileRole.SOURCE
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append((rel, "cpp", digest, role.value, 0, 0, "utf-8", "lf"))
            if '#include "geo.h"' in text:
                deps.append((rel, "include/geo.h", "include"))
        with IndexStore.open(path, reset=True) as store:
            store.write(files=rows, dependencies=deps)
            meta = IndexMeta(adapter="fake", adapter_version="0", tool_versions={"fake": "1"})
            store.set_meta(meta)
        return SymbolIndex(path=path, meta=meta)


def reply_with(*calls: Any) -> CompletionResult:
    from ..agent.conftest import reply

    return reply(*calls)


@dataclass
class EngineWorld:
    repo: Path
    tmp: Path
    git: GitRunner
    adapter: IndexingAdapter = field(default_factory=IndexingAdapter)
    asked: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def manifest(
        self,
        *,
        plan: str = "auto",
        unit: str = "none",
        extra: str = "",
        parity: str = "{baseline_runs: 1}",
    ) -> Path:
        path = self.repo / "migration_config.yaml"
        text = _MANIFEST.format(plan=plan, unit=unit, extra=extra, parity=parity)
        path.write_text(text, encoding="utf-8")
        return path

    def session(
        self, config_path: Path, *, run_id: str | None = None, resume: bool = False
    ) -> RunSession:
        loaded = load_config(config_path)
        rid = run_id or new_run_id()
        if resume:
            workspace = RunWorkspace.resume(self.repo, rid, base_commit=self._base(rid))
        else:
            workspace = RunWorkspace.create(self.repo, rid)
        artifacts = StateDir.for_repo(self.repo).run(rid).ensure()
        log_dir = artifacts.root / "logs"
        log_dir.mkdir(exist_ok=True)
        ctx = AdapterContext(
            worktree=workspace.worktree.path,
            build_dir=workspace.build_dir,
            cache_dir=workspace.cache_dir,
            log_dir=log_dir,
            executor=PathOnlyExecutor(),  # type: ignore[arg-type]
            lang=language_config(loaded.config),
        )
        registry = RuleRegistry(rules={TYPEDEFS.rule.id: TYPEDEFS})
        return RunSession(
            config_path=config_path,
            loaded=loaded,
            adapter=self.adapter,
            rules=registry,
            workspace=workspace,
            artifacts=artifacts,
            executor=ctx.executor,
            ctx=ctx,
            warnings=[],
        )

    def _base(self, run_id: str) -> str:
        with Store.open(StateDir.for_repo(self.repo).db_path) as store:
            record = store.get_run(run_id)
            assert record is not None
            return record.base_commit or ""

    def store(self) -> Store:
        return Store.open(StateDir.for_repo(self.repo).db_path)

    def options(
        self, *, decide: Callable[[dict[str, Any]], ApprovalRecord | None] | None = None, **kw: Any
    ) -> RunOptions:
        def ask(request: dict[str, Any]) -> ApprovalRecord | None:
            self.asked.append(request)
            return decide(request) if decide else None

        return RunOptions(ask=ask if decide else None, poll_s=0.01, **kw)

    def run(
        self,
        config_path: Path,
        script: list[Responder],
        *,
        options: RunOptions | None = None,
        run_id: str | None = None,
        resume: bool = False,
        on_engine: Callable[[RunEngine], None] | None = None,
        agent: AgentAction | None = None,
    ) -> tuple[RunResult, ScriptedProvider, RunSession]:
        """Start (or resume) a run and drive it until it ends or parks. ``agent`` is a host
        agent's action on the unit that waits for it (``unit submit|abort``)."""
        session = self.session(config_path, run_id=run_id, resume=resume)
        scripted = ScriptedProvider(list(script))
        options = options or self.options()

        def factory(ledger: UsageLedger) -> LLMService:
            return LLMService(session.config.llm, ledger=ledger, providers={"anthropic": scripted})

        with self.store() as store:
            if not resume:
                store.create_run(new_run_record(session))
            engine = RunEngine(session, store, options=options, llm_factory=factory)
            if not resume:
                store.set_run_environment(
                    session.run_id, run_environment(session, engine.llm, host=options.host)
                )
            if on_engine is not None:
                on_engine(engine)
            try:
                result = engine.drive(fresh=not resume, agent=agent)
            except BaseException:
                session.workspace.park()  # as open_session does: the run stays resumable
                raise
            finally:
                engine.graph.close()
        _end_session(session, result)
        if session.park:
            session.workspace.park()
        else:
            session.workspace.cleanup(delete_branch=session.delete_branch)
        return result, scripted, session

    def approval(
        self,
        run_id: str,
        decision: ApprovalDecision,
        *,
        gate: ApprovalGate = ApprovalGate.PLAN,
        unit_id: str | None = None,
        exclude: list[str] | None = None,
        reason: str | None = None,
    ) -> ApprovalRecord:
        from datetime import UTC, datetime

        return ApprovalRecord(
            run_id=run_id,
            gate=gate,
            unit_id=unit_id,
            decision=decision,
            exclude=exclude or [],
            reason=reason,
            source="test",
            created_at=datetime.now(UTC),
        )

    def log(self, branch: str) -> list[str]:
        out = self.git(self.repo, "log", "--format=%B%x00", f"main..{branch}")
        return [m.strip() for m in out.split("\0") if m.strip()]


@pytest.fixture
def engine_world(tmp_path: Path, git: GitRunner) -> EngineWorld:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "include").mkdir()
    (repo / "tests").mkdir()
    (repo / "CMakeLists.txt").write_text("project(geo)\n", encoding="utf-8")
    (repo / "include" / "geo.h").write_text(
        f"#pragma once\ntypedef double Scalar;\n{MACRO}\nDECLARE(int, Id);\n", encoding="utf-8"
    )
    (repo / "src" / "geo.cpp").write_text(
        '#include "geo.h"\nScalar area(Scalar w, Scalar h) { return w * h; }\n', encoding="utf-8"
    )
    (repo / "tests" / "geo_test.cpp").write_text(
        '#include "geo.h"\nTEST(Geo, Area) { EXPECT_EQ(4, 4); }\n', encoding="utf-8"
    )
    git(tmp_path, "init", "--quiet", str(repo))
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "legacy")
    return EngineWorld(repo=repo, tmp=tmp_path, git=git)
