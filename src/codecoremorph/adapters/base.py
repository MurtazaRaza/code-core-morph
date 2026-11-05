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
from collections.abc import Collection
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
    ProjectDetection,
    RuleSite,
    SymbolIndex,
    TestAssertions,
    TestReport,
    TestSelection,
    ToolchainSpec,
)
from codecoremorph.core.plugins import ADAPTER_API
from codecoremorph.sandbox.executor import Executor

ADAPTER_API_VERSION = ADAPTER_API.version


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
    """Base class of every language adapter. Subclasses set ``language`` and declare the
    adapter API version they implement, ``api_version = "1"`` (FR-15.3)."""

    api_version: ClassVar[str]
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

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        """The settings of a starter manifest for the repository at ``root`` (FR-1.4), read
        from ``files``: the files git tracks there, including those of checked-out
        submodules, as POSIX paths relative to ``root``. None when the repository has no code
        in this adapter's language outside vendored directories.

        ``codecoremorph init`` calls it on the user's own checkout, so it only reads files:
        it runs no command and writes nothing."""
        return None

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

    # Characterization tests (SPEC §7.11). An adapter that declares ``register_test_files``
    # implements every method in this group; ``extract_assertions`` alone serves I-4.

    def characterization_test_file(self, test_dir: str, stem: str) -> str:
        """The worktree path of a new characterization test file named after ``stem`` (a file
        stem such as ``counter``) in ``test_dir``."""
        raise AdapterNotSupportedError(f"{self.language}: register_test_files")

    def register_test_files(
        self, ctx: AdapterContext, test_dir: str, files: list[str], sources: list[str]
    ) -> list[str]:
        """Make the build compile and run the characterization tests ``files`` (worktree paths
        in ``test_dir``), linked with whatever builds ``sources`` (the files defining the code
        they test); with no ``files``, take them out of the build. Writes only build files
        inside ``ctx.worktree`` and returns their worktree paths. ``characterization_tests``
        selects them for ``run_tests``."""
        raise AdapterNotSupportedError(f"{self.language}: register_test_files")

    def characterization_tests(self, test_ids: list[str]) -> TestSelection:
        """The selection that runs only the characterization tests ``test_ids``."""
        raise AdapterNotSupportedError(f"{self.language}: register_test_files")

    def check_determinism(self, ctx: AdapterContext, files: list[str]) -> list[str]:
        """``path:line: reason`` for each use, in the test files ``files``, of the wall clock,
        randomness, the filesystem, the network or thread timing (FR-7.3)."""
        raise AdapterNotSupportedError(f"{self.language}: register_test_files")

    def drop_tests(self, ctx: AdapterContext, file: str, test_ids: list[str]) -> int:
        """Remove the test cases ``test_ids`` from ``file`` (a worktree path); returns how many
        test cases the file has left."""
        raise AdapterNotSupportedError(f"{self.language}: register_test_files")

    def extract_assertions(self, ctx: AdapterContext, files: list[str]) -> list[TestAssertions]:
        """Every test case in ``files`` (worktree paths; missing ones are skipped) with its
        test ID and its assertions, normalized so that layout changes don't show (I-4)."""
        raise AdapterNotSupportedError(f"{self.language}: extract_assertions")

    def query(self, ctx: AdapterContext, name: str, files: list[str]) -> list[RuleSite]:
        """Answer a rule detector query listed in ``capabilities().queries``."""
        raise AdapterNotSupportedError(f"{self.language}: query {name}")

    # Ports behind a C interface (D-56). The source adapter reads C headers; the target
    # adapter says which headers its new code exports.

    def c_declarations(self, ctx: AdapterContext, header: Path) -> dict[str, str]:
        """The functions the C header ``header`` declares (a host path in ``ctx.worktree`` or
        a cache directory, which the sandbox sees), by name, each with its canonical C
        signature (typedefs resolved, parameter names left out), e.g.
        ``{"tk_ring_len": "unsigned long long (const struct tk_ring *)"}``. Functions the
        header defines (``static inline``) are not part of the interface and are left out.
        Runs commands through ``ctx.executor`` only."""
        raise AdapterNotSupportedError(f"{self.language}: c_declarations")

    def exported_c_headers(self, ctx: AdapterContext, files: list[str]) -> list[Path]:
        """C headers for what the code holding ``files`` (worktree paths) exports through a C
        interface, generated under ``ctx.cache_dir``, one per package (a Rust crate)."""
        raise AdapterNotSupportedError(f"{self.language}: exported_c_headers")

    # Module ports behind façades (D-58), on the target adapter.

    def port_names(self, build_target: str, stem: str, taken: Collection[str]) -> tuple[str, str]:
        """``(package, module)`` for a module of the source's ``build_target`` whose files have
        the stem ``stem``: the package that takes over the target, and a module name valid in
        this language and not in ``taken`` (the package's other modules)."""
        raise AdapterNotSupportedError(f"{self.language}: port_names")

    def symbol_map_path(self) -> str:
        """Where the project keeps its symbol map (worktree-relative): a TOML file of
        ``[[symbol]]`` entries naming a source symbol (under the source language's name) and
        the target items that took it over (under this language's name)."""
        raise AdapterNotSupportedError(f"{self.language}: symbol_map_path")

    def unsafe_sites(self, ctx: AdapterContext) -> list[RuleSite]:
        """Places where the port's packages allow unsafe code outside their bridge."""
        raise AdapterNotSupportedError(f"{self.language}: unsafe_sites")

    def lint(self, ctx: AdapterContext) -> list[Diagnostic]:
        """The linter's diagnostics over the port's packages (Rust: clippy)."""
        raise AdapterNotSupportedError(f"{self.language}: lint")
