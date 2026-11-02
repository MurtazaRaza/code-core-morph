"""Command results from the sandbox (SPEC §7.8)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from codecoremorph.core.models.base import CoreModel


class ExecResult(CoreModel):
    """One command run by an executor. ``stdout`` and ``stderr`` are capped; ``log_path`` has
    the full output."""

    argv: list[str]
    exit_code: int = Field(description="-1 when the process was killed and left no exit code")
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_s: float = Field(ge=0)
    timed_out: bool = False
    log_path: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out
