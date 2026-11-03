"""Runs a fake-adapter engine in its own process, for the crash tests (NFR-2.4).

    python crash_driver.py REPO start|resume CRASH

CRASH names where the process dies at once (``os._exit``: no cleanup, like a kill):
``llm:N`` in the N-th model call, ``build:N`` in the N-th build, ``standard:N`` in the N-th
standard detection (the first one follows a unit's commit, before the run's checkpoint), or
``none``. The run ID goes to ``REPO/../run-id``; the result is printed as JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.unit.agent.conftest import replace_edit, reply, submit  # noqa: E402
from tests.unit.orchestrator.conftest import (  # noqa: E402
    FIXED,
    MACRO,
    EngineWorld,
    IndexingAdapter,
)

from codecoremorph.adapters.base import AdapterContext  # noqa: E402
from codecoremorph.core.models import (  # noqa: E402
    BuildResult,
    CompletionRequest,
    CompletionResult,
)

CRASH_EXIT = 42


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, check=True, text=True
    ).stdout


class Crashing(IndexingAdapter):
    def __init__(self, crash: str) -> None:
        super().__init__()
        what, _, number = crash.partition(":")
        self.what = what
        self.at = int(number or 0)
        self.counts: dict[str, int] = {}

    def tick(self, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1
        if what == self.what and self.counts[what] == self.at:
            os._exit(CRASH_EXIT)

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        self.tick("build")
        return super().build(ctx, targets)

    def project_standard(self, ctx: AdapterContext) -> str | None:
        self.tick("standard")
        return None


def main() -> None:
    repo, mode, crash = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    world = EngineWorld(repo=repo, tmp=repo.parent, git=_git)
    adapter = Crashing(crash)
    world.adapter = adapter
    config = repo / "migration_config.yaml"
    id_file = repo.parent / "run-id"

    def model(request: CompletionRequest) -> CompletionResult:
        adapter.tick("llm")
        return reply(submit("s1", replace_edit("include/geo.h", MACRO, FIXED)))

    if mode == "start":
        from codecoremorph.core.ids import new_run_id

        run_id = new_run_id()
        id_file.write_text(run_id, encoding="utf-8")
        result, _, _ = world.run(config, [model], run_id=run_id)
    else:
        run_id = id_file.read_text(encoding="utf-8")
        result, _, _ = world.run(config, [model], run_id=run_id, resume=True)
    outcome = {"status": result.status.value, "parked": result.parked, "branch": result.branch}
    sys.stdout.write(json.dumps(outcome) + "\n")


if __name__ == "__main__":
    main()
