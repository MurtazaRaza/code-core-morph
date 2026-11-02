"""Phase 1 exit criterion: create worktree -> apply a 3-file edit batch -> run a command ->
roll back -> commit -> clean up, in each executor mode. Docker mode runs under ``-m docker``."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.config.models import NetworkPolicy
from codecoremorph.core.models import EditBatch
from codecoremorph.patching import PatchEngine
from codecoremorph.sandbox import Executor, NativeExecutor
from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
from codecoremorph.vcs import RunWorkspace, parse_trailers, unit_commit_message

RUN = "20261102-141500-a3f9"
CHECK = (
    "import pathlib, sys\n"
    "text = pathlib.Path('src/vector.h').read_text()\n"
    "ok = 'using Index' in text and pathlib.Path('src/added.h').exists()\n"
    "print('verified' if ok else 'unverified'); sys.exit(0 if ok else 1)\n"
)


def _executor(kind: str, workspace: RunWorkspace, tmp_path: Path) -> tuple[Executor, list[str]]:
    if kind == "native":
        import sys

        return NativeExecutor(log_dir=tmp_path / "logs"), [sys.executable, "-c", CHECK]
    settings = DockerSettings(
        image="python:3.12-alpine",
        run_id=RUN,
        worktree=workspace.worktree.path,
        build_dir=workspace.build_dir,
        cache_dir=workspace.cache_dir,
        log_dir=tmp_path / "logs",
        network=NetworkPolicy.OFF,
        cpus=1,
        memory="512m",
    )
    return DockerExecutor(settings), ["python", "-c", CHECK]


@pytest.mark.parametrize("kind", ["native", pytest.param("docker", marks=pytest.mark.docker)])
def test_scripted_scenario(kind: str, repo: Path, tmp_path: Path, git) -> None:
    workspace = RunWorkspace.create(repo, RUN)
    executor, check = _executor(kind, workspace, tmp_path)
    executor.start()
    try:
        worktree = workspace.worktree
        engine = PatchEngine(worktree.path)
        cwd = executor.to_sandbox_path(worktree.path)

        # Attempt 1: a 3-file batch, verified by a command, then rolled back.
        snapshot = worktree.snapshot()
        batch = EditBatch.model_validate(
            {
                "edits": [
                    {
                        "op": "replace",
                        "path": "src/vector.h",
                        "search": "typedef int Index;",
                        "replace": "using Index = int;",
                    },
                    {
                        "op": "replace",
                        "path": "src/vector.cpp",
                        "search": "return 0;",
                        "replace": "return 0; // unchanged behavior",
                    },
                    {"op": "create", "path": "src/added.h", "content": "#pragma once\n"},
                ],
                "summary": "Replaced 1 typedef declaration with an alias declaration.",
            }
        )
        applied = engine.apply(batch)
        assert applied.applied, applied.feedback()
        assert len(applied.changed_files) == 3
        result = executor.run(check, cwd=cwd, timeout_s=120)
        assert result.ok, result.stderr
        assert result.stdout.strip() == "verified"
        worktree.rollback(snapshot)
        assert not worktree.has_changes()
        assert not executor.run(check, cwd=cwd, timeout_s=120).ok

        # Attempt 2: the same batch, committed with the §7.9 message format.
        assert engine.apply(batch).applied
        diff = worktree.diff(snapshot)
        assert sorted(diff.paths) == ["src/added.h", "src/vector.cpp", "src/vector.h"]
        message = unit_commit_message(
            run_id=RUN,
            unit_id="U-0001",
            rules=["replace_typedefs_with_using"],
            files=diff.paths,
            summary=batch.summary,
            attempts=2,
            models=[],
        )
        sha = worktree.commit(message)
        assert (
            parse_trailers(git(repo, "log", "-1", "--format=%B", sha))["CodeCoreMorph-Attempts"]
            == "2"
        )
    finally:
        executor.stop()
        workspace.cleanup()
    assert not workspace.worktree.path.exists()
    assert git(repo, "rev-parse", f"codecoremorph/{RUN}").strip() == sha
