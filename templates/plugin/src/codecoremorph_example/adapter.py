"""The skeleton of a language adapter (SPEC §7.1).

An adapter wraps one language's tools: configure, build, test, index and codemods. Every
command goes through ``ctx.executor`` (never ``subprocess``), so it runs in the Docker sandbox
or natively as the user chose. Replace each ``NotImplementedError`` with the real thing; the
built-in C++ adapter (``codecoremorph.adapters.cpp``) shows a complete one.
"""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext, LanguageAdapter
from codecoremorph.core.models import (
    AdapterCapabilities,
    BuildResult,
    CodemodResult,
    CodemodSpec,
    LanguageConfig,
    ProjectDetection,
    SymbolIndex,
    TestReport,
    TestSelection,
    ToolchainSpec,
)


class ExampleAdapter(LanguageAdapter):
    api_version = "1"  # the adapter API this class implements (FR-15.3)
    language = "example"  # what a manifest's migration.source.language names
    version = "0.1.0"  # the adapter's own version

    def capabilities(self) -> AdapterCapabilities:
        # The standards (language versions) the adapter builds and migrates, oldest first.
        return AdapterCapabilities(standards=["example1", "example2"])

    def toolchain_requirements(self, lang: LanguageConfig) -> ToolchainSpec:
        # The image a Docker-mode run uses, and the tools a native run checks for.
        return ToolchainSpec(language=self.language, image="codecoremorph-example")

    def detect_project(self, root: Path, files: list[str]) -> ProjectDetection | None:
        # Optional: what `codecoremorph init` writes for a repository of this language.
        return None

    def configure(self, ctx: AdapterContext) -> BuildResult:
        raise NotImplementedError("prepare an out-of-source build in ctx.build_dir")

    def build(self, ctx: AdapterContext, targets: list[str] | None = None) -> BuildResult:
        raise NotImplementedError("build and collect every diagnostic")

    def run_tests(self, ctx: AdapterContext, selection: TestSelection | None = None) -> TestReport:
        raise NotImplementedError("run the tests, with stable framework-qualified test IDs")

    def index(self, ctx: AdapterContext, files: list[str] | None = None) -> SymbolIndex:
        raise NotImplementedError("index symbols, references and file dependencies")

    def run_codemods(
        self, ctx: AdapterContext, codemods: list[CodemodSpec], files: list[str]
    ) -> CodemodResult:
        raise NotImplementedError("apply deterministic fixes to these files only")
