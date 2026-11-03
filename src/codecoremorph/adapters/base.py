"""The language adapter contract (SPEC §7.1).

An adapter wraps one language's tooling: configure, build, test, index and codemods. The core
finds adapters through the ``codecoremorph.adapters`` entry point and never imports one
directly (NFR-3.1).

Rules every adapter follows:

- Every command goes through ``ctx.executor``; adapters never start processes themselves.
- Codemods, formatters and test registration may write files only inside ``ctx.worktree``.
  The core captures those writes in the attempt diff and checks them like any other change.
- Heavy analysis runs as a helper program inside the sandbox and emits normalized JSON.
- Adapters are stateless between calls, apart from ``ctx.cache_dir``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodResult,
    CodemodSpec,
    Diagnostic,
    LanguageConfig,
    RuleSite,
    SymbolIndex,
    TestReport,
    TestSelection,
    ToolchainSpec,
)
from codecoremorph.sandbox.executor import Executor

ADAPTER_API_VERSION = "1"


@dataclass(frozen=True)
class AdapterContext:
    """Everything an adapter call may use. Paths are host paths; convert them with
    ``executor.to_sandbox_path`` before putting them on a command line."""

    worktree: Path
    build_dir: Path  # outside the worktree
    cache_dir: Path  # adapter-private cache (index, tool caches); shared with the sandbox
    log_dir: Path  # where adapters put their own logs and reports
    executor: Executor
    lang: LanguageConfig

    def sandbox(self, host_path: Path) -> str:
        return self.executor.to_sandbox_path(host_path)


class AdapterNotSupportedError(NotImplementedError):
    """Raised by an optional method the adapter doesn't declare in ``capabilities()``."""


class LanguageAdapter(ABC):
    """Base class of every language adapter. Subclasses set ``language``."""

    api_version: ClassVar[str] = ADAPTER_API_VERSION
    language: ClassVar[str]
    version: ClassVar[str] = "0"

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities: ...

    @abstractmethod
    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        """The image (Docker mode) and the tools with minimum versions (native mode)."""

    @abstractmethod
    def configure(self, ctx: AdapterContext) -> BuildResult:
        """Prepare an out-of-source build in ``ctx.build_dir``."""

    @abstractmethod
    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        """Build, collecting every diagnostic; warnings never fail a build."""

    @abstractmethod
    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        """Run the tests with stable, framework-qualified test IDs."""

    @abstractmethod
    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        """Index the project (or re-index ``files``) into ``ctx.cache_dir``."""

    @abstractmethod
    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        """Apply deterministic fixes, changing only ``files`` (worktree-relative)."""

    # ------------------------------------------------------------------ optional

    def project_standard(self, ctx: AdapterContext) -> str | None:
        """The standard the project's own build files set in ``ctx.worktree`` (the oldest, when
        they set several), or None when they set none or the adapter can't tell.

        The core builds with the newer of this and the manifest's source standard, so a unit
        based on a commit that already has the standard bump builds at the new standard
        (D-42). Reads files only; runs no command."""
        return None

    def clean_build(self, ctx: AdapterContext) -> None:
        """Empty the build tree so the next ``configure`` and ``build`` start from nothing (the
        run's final verification, SPEC §7.5). Runs commands through ``ctx.executor`` only."""
        raise AdapterNotSupportedError(f"{self.language}: clean_build")

    def parse_check(self, ctx: AdapterContext, files: list[str]) -> list[Diagnostic]:
        raise AdapterNotSupportedError(f"{self.language}: parse_check")

    def format(self, ctx: AdapterContext, files: list[str]) -> None:
        raise AdapterNotSupportedError(f"{self.language}: format")

    def detect_disabled_tests(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        """Markers that disable or skip tests, as ``path:line: marker`` strings (I-3)."""
        raise AdapterNotSupportedError(f"{self.language}: detect_disabled_tests")

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        """Answer a rule detector query listed in ``capabilities().queries``."""
        raise AdapterNotSupportedError(f"{self.language}: query {name}")
