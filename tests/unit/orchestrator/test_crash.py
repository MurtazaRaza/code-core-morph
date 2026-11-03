"""Crash consistency (NFR-2.4): the engine dies abruptly at chosen points (no cleanup runs, as
with a kill); ``resume`` then finishes the run, and every commit on the branch is green."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codecoremorph.core.models import RunStatus
from codecoremorph.vcs.lock import read_lock

from .conftest import EngineWorld

DRIVER = Path(__file__).with_name("crash_driver.py")
CRASH_EXIT = 42

pytestmark = pytest.mark.slow


def _drive(repo: Path, mode: str, crash: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DRIVER), str(repo), mode, crash],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize(
    "crash",
    [
        "build:1",  # the baseline (build 1)
        "build:2",  # the unit's first verification, after its codemods
        "llm:1",  # mid-call: the model's first reply never arrives
        "build:3",  # the verification of the agent's edits
        "standard:1",  # after the unit's commit, before the run's checkpoint
        "build:4",  # the final verification
    ],
)
def test_a_killed_run_resumes_and_completes(engine_world: EngineWorld, crash: str) -> None:
    config = engine_world.manifest()
    repo = engine_world.repo
    killed = _drive(repo, "start", crash)
    assert killed.returncode == CRASH_EXIT, killed.stderr[-2000:]
    run_id = (repo.parent / "run-id").read_text(encoding="utf-8")
    holder = read_lock(repo / ".codecoremorph" / "lock")
    assert holder is not None
    assert holder.run_id == run_id  # a dead engine's lock: stale, so resume takes it over
    assert not holder.parked

    resumed = _drive(repo, "resume", "none")
    assert resumed.returncode == 0, resumed.stderr[-3000:]
    outcome = json.loads(resumed.stdout.strip().splitlines()[-1])
    assert outcome["status"] == RunStatus.COMPLETED.value
    commits = engine_world.git(repo, "rev-list", "--reverse", f"main..{outcome['branch']}").split()
    assert len(commits) == 1  # the unit committed once, even when it crashed after committing
    for commit in commits:
        header = engine_world.git(repo, "show", f"{commit}:include/geo.h")
        assert "BROKEN" not in header
        assert "using N = T" in header
    assert not (repo / ".codecoremorph" / "lock").exists()
    assert config.is_file()  # the user's untracked manifest survived both processes
