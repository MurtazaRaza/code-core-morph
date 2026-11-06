"""The git CLI, run on the host (SPEC §7.9). The only place a git process is started.

Every invocation is hardened (D-29):

- hooks are disabled (``core.hooksPath`` points at the null device), so neither the user's
  hooks nor anything planted in the repository runs during a run;
- commit and tag signing are off, so no passphrase prompt can stall a run;
- ``--no-optional-locks`` keeps read-only commands such as ``status`` from rewriting the
  user's index;
- environment variables that redirect git to another repository or index (``GIT_DIR``,
  ``GIT_INDEX_FILE``, ...) are removed, and terminal prompts are disabled.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.errors import GitCommandError, HostEnvironmentError

log = logging.getLogger(__name__)

MIN_GIT_VERSION = (2, 30)
_VERSION_RE = re.compile(r"git version (\d+)\.(\d+)(?:\.(\d+))?")
_DEFAULT_TIMEOUT_S = 600.0

# `git rev-parse --local-env-vars`, plus the namespace and the -c transport variables.
_REDIRECTING_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_OBJECT_DIRECTORY",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_GRAFT_FILE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_PREFIX",
        "GIT_SHALLOW_FILE",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
    }
)
_HARDENING = (
    "--no-optional-locks",
    "-c",
    f"core.hooksPath={os.devnull}",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "core.quotePath=false",
    "-c",
    "color.ui=false",
)
# A checkout as on Linux: the committed bytes, LF for text a .gitattributes normalizes, and
# whatever eol a .gitattributes sets explicitly; the machine's core.autocrlf doesn't apply.
LF_CHECKOUT = ("core.autocrlf=false", "core.eol=lf")


def git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise HostEnvironmentError(
            "git was not found on PATH",
            reason="CodeCoreMorph needs git >= 2.30 for worktrees, snapshots and commits.",
            hint="Install git (https://git-scm.com/downloads) and open a new terminal.",
        )
    return executable


def git_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment git runs with: redirecting variables removed, prompts disabled."""
    env = {
        key: value
        for key, value in (os.environ if base is None else base).items()
        if key.upper() not in _REDIRECTING_ENV
        and not key.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_EDITOR"] = "true"  # git runs it through sh, Git for Windows included
    return env


@dataclass(frozen=True)
class GitResult:
    argv: list[str]
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        """stdout decoded as UTF-8, without the trailing newline."""
        return self.stdout.decode("utf-8", errors="replace").rstrip("\n")


class Git:
    """Runs git in one directory. Construct one per repository or worktree. ``config``
    (``key=value`` settings, such as :data:`LF_CHECKOUT`) applies to every command."""

    def __init__(
        self, cwd: Path, *, timeout_s: float = _DEFAULT_TIMEOUT_S, config: Sequence[str] = ()
    ):
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.config = tuple(config)

    def run(
        self,
        *args: str,
        check: bool = True,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> GitResult:
        settings = [arg for setting in self.config for arg in ("-c", setting)]
        argv = [git_executable(), *_HARDENING, *settings, *args]
        child_env = git_environment()
        if env:
            child_env.update(env)
        log.debug("git %s (in %s)", " ".join(args), self.cwd)
        try:
            proc = subprocess.run(
                argv,
                cwd=self.cwd,
                input=stdin,
                capture_output=True,
                env=child_env,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError(
                f"`git {args[0]}` timed out after {self.timeout_s:.0f} s",
                argv=argv,
                returncode=None,
            ) from exc
        except OSError as exc:
            raise GitCommandError(
                f"couldn't run `git {args[0]}`", argv=argv, returncode=None, stderr=str(exc)
            ) from exc
        result = GitResult(argv, proc.returncode, proc.stdout, proc.stderr)
        if check and proc.returncode != 0:
            raise GitCommandError(
                f"`git {' '.join(args[:2])}` failed with exit code {proc.returncode}",
                argv=argv,
                returncode=proc.returncode,
                stderr=proc.stderr.decode("utf-8", errors="replace"),
            )
        return result

    def text(self, *args: str) -> str:
        return self.run(*args).text


def parse_git_version(text: str) -> tuple[int, int, int]:
    """Parse ``git --version`` output, e.g. ``git version 2.43.0.windows.1``."""
    match = _VERSION_RE.search(text)
    if match is None:
        raise ValueError(f"unrecognized `git --version` output: {text.strip()!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def git_version() -> tuple[int, int, int]:
    """The installed git's version. Raises :class:`HostEnvironmentError` if git is missing."""
    executable = git_executable()
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostEnvironmentError(f"couldn't run {executable}", reason=str(exc)) from exc
    if result.returncode != 0:
        raise HostEnvironmentError(
            f"`git --version` failed with exit code {result.returncode}",
            reason=result.stderr.strip() or None,
        )
    return parse_git_version(result.stdout)
