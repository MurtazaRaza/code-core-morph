"""Paths inside the state directory ``<repo>/.codecoremorph/`` (SPEC §7.12).

```
.codecoremorph/
  codecoremorph.db  lock
  runs/<run-id>/
    plan.json  report.md  report.json  checkpoints.sqlite
    attempts/<unit-id>/<n>/
      diff.patch  build.log  test.log  transcript.jsonl  feedback.md
```

Run and unit IDs are validated before they become path components, so a crafted ID can't
escape the state directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.ids import validate_run_id, validate_unit_id
from codecoremorph.core.paths import STATE_DIR_NAME

__all__ = ["STATE_DIR_NAME", "AttemptArtifacts", "RunArtifacts", "StateDir"]


@dataclass(frozen=True)
class AttemptArtifacts:
    root: Path

    @property
    def diff_patch(self) -> Path:
        return self.root / "diff.patch"

    @property
    def build_log(self) -> Path:
        return self.root / "build.log"

    @property
    def test_log(self) -> Path:
        return self.root / "test.log"

    @property
    def transcript_jsonl(self) -> Path:
        return self.root / "transcript.jsonl"

    @property
    def feedback_md(self) -> Path:
        return self.root / "feedback.md"

    def ensure(self) -> AttemptArtifacts:
        self.root.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class RunArtifacts:
    root: Path

    @property
    def plan_json(self) -> Path:
        return self.root / "plan.json"

    @property
    def report_md(self) -> Path:
        return self.root / "report.md"

    @property
    def report_json(self) -> Path:
        return self.root / "report.json"

    @property
    def checkpoints_db(self) -> Path:
        return self.root / "checkpoints.sqlite"

    def unit_dir(self, unit_id: str) -> Path:
        """A unit's directory: its attempts, and what its invariants compare against."""
        return self.root / "attempts" / validate_unit_id(unit_id)

    def attempt(self, unit_id: str, number: int) -> AttemptArtifacts:
        if number < 1:
            raise ValueError(f"attempt numbers start at 1, got {number}")
        return AttemptArtifacts(self.unit_dir(unit_id) / str(number))

    def ensure(self) -> RunArtifacts:
        self.root.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class StateDir:
    root: Path

    @classmethod
    def for_repo(cls, repo_root: Path) -> StateDir:
        return cls(repo_root / STATE_DIR_NAME)

    @property
    def db_path(self) -> Path:
        return self.root / "codecoremorph.db"

    @property
    def lock_path(self) -> Path:
        return self.root / "lock"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def baselines_dir(self) -> Path:
        """Baselines by commit, reused by units started from that commit (D-42)."""
        return self.root / "baselines"

    def run(self, run_id: str) -> RunArtifacts:
        return RunArtifacts(self.runs_dir / validate_run_id(run_id))
