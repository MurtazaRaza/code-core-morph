"""Location of every file CodeCoreMorph manages outside a repository (SPEC §7.15).

``CODECOREMORPH_HOME`` defaults to:

- Windows: ``%LOCALAPPDATA%\\codecoremorph``
- macOS: ``$HOME/Library/Caches/codecoremorph``
- Linux and others: ``${XDG_CACHE_HOME:-$HOME/.cache}/codecoremorph``

The ``CODECOREMORPH_HOME`` environment variable overrides the default; a relative value is
resolved against the current directory. ``scripts/lib/common.{sh,bat}`` resolve it the same way,
and the two implementations must stay in step.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from codecoremorph.core.ids import validate_run_id

HOME_ENV_VAR = "CODECOREMORPH_HOME"
STATE_DIR_NAME = ".codecoremorph"  # per-repository state directory (SPEC §7.9)
_DIR_NAME = "codecoremorph"


def _is_windows(platform: str) -> bool:
    return platform == "win32"


def _is_absolute(value: str, platform: str) -> bool:
    if _is_windows(platform):
        return PureWindowsPath(value).is_absolute()
    return PurePosixPath(value).is_absolute()


def codecoremorph_home(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    cwd: Path | None = None,
) -> Path:
    """Return the CodeCoreMorph home directory. It is not created.

    ``env``, ``platform`` (a ``sys.platform`` value) and ``cwd`` default to the running
    process; tests pass them explicitly.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform

    override = env.get(HOME_ENV_VAR, "").strip()
    if override:
        expanded = str(Path(override).expanduser())
        if _is_absolute(expanded, platform):
            return Path(expanded)
        return (cwd if cwd is not None else Path.cwd()) / expanded

    if _is_windows(platform):
        local = env.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else _user_home(env) / "AppData" / "Local"
        return base / _DIR_NAME
    if platform == "darwin":
        return _user_home(env) / "Library" / "Caches" / _DIR_NAME
    xdg = env.get("XDG_CACHE_HOME", "").strip()
    # The XDG spec says relative values must be ignored.
    if xdg and _is_absolute(xdg, platform):
        return Path(xdg) / _DIR_NAME
    return _user_home(env) / ".cache" / _DIR_NAME


def _user_home(env: Mapping[str, str]) -> Path:
    for var in ("HOME", "USERPROFILE"):
        value = env.get(var, "").strip()
        if value:
            return Path(value)
    return Path.home()


@dataclass(frozen=True)
class HomeLayout:
    """Directory layout under ``CODECOREMORPH_HOME`` (SPEC §7.15)."""

    root: Path

    @classmethod
    def resolve(cls) -> HomeLayout:
        return cls(codecoremorph_home())

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def toolchains(self) -> Path:
        return self.root / "toolchains"

    def toolchain_env(self, language: str) -> Path:
        return self.toolchains / f"{language}.env"

    def worktree(self, run_id: str) -> Path:
        return self.root / "worktrees" / validate_run_id(run_id)

    def build_dir(self, run_id: str) -> Path:
        return self.root / "build" / validate_run_id(run_id)

    def run_cache(self, run_id: str) -> Path:
        """Adapter-private cache of a run (index, tool caches), outside the worktree."""
        return self.root / "cache" / validate_run_id(run_id)
