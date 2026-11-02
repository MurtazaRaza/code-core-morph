"""Models that cross the language-adapter boundary (SPEC §7.1, §7.8)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import Field

from codecoremorph.core.models.base import CoreModel, WorktreePath
from codecoremorph.core.models.verification import Diagnostic


class ToolRequirement(CoreModel):
    """A tool the toolchain must provide, checked natively before a run (FR-1.3)."""

    name: str = Field(min_length=1, description='Program name, e.g. "clang-tidy"')
    min_version: str | None = None
    version_args: list[str] = Field(default_factory=lambda: ["--version"])
    purpose: str = ""


class ToolchainSpec(CoreModel):
    """What an adapter needs from the sandbox: an image in Docker mode, tools natively."""

    language: str
    image: str = Field(description='Image repository name, e.g. "codecoremorph-cpp"')
    tools: list[ToolRequirement] = Field(default_factory=list[ToolRequirement])
    install_hint: str = Field(default="", description="How to install the tools natively")


class AdapterCapabilities(CoreModel):
    """The optional adapter methods this adapter implements (SPEC §7.1)."""

    parse_check: bool = False
    format: bool = False
    register_test_files: bool = False
    extract_assertions: bool = False
    detect_disabled_tests: bool = False
    scaffold_project: bool = False
    codemod_tools: list[str] = Field(default_factory=list[str])
    queries: list[str] = Field(default_factory=list[str], description="Detector queries")
    standards: list[str] = Field(
        default_factory=list[str],
        description="The language standards the adapter builds and migrates, oldest first, e.g. "
        '["cpp98", "cpp03", "cpp11"]. Rules and manifests compare standards in this order.',
    )


class LanguageConfig(CoreModel):
    """The slice of the manifest an adapter needs."""

    language: str
    standard: str = Field(description='The standard to build with, e.g. "cpp11"')
    target_standard: str | None = Field(
        default=None, description="The migration's target standard (for the standard bump)"
    )
    build_system: str | None = None
    configure_args: list[str] = Field(default_factory=list[str])
    build_targets: list[str] = Field(default_factory=list[str])
    source_dirs: list[str] = Field(default_factory=lambda: ["."])
    test_dirs: list[str] = Field(default_factory=list[str])
    exclude: list[str] = Field(default_factory=list[str])
    public_api: list[str] = Field(default_factory=list[str])
    build_timeout_s: float = 900
    test_timeout_s: float = 1800


class TestSelection(CoreModel):
    __test__: ClassVar[bool] = False  # not a pytest test class

    test_ids: list[str] | None = Field(default=None, description="None runs every test")


class CodemodChange(CoreModel):
    file: WorktreePath
    replacements: int = Field(ge=1)


class SkippedReplacement(CoreModel):
    file: str
    check: str
    reason: str


class CodemodResult(CoreModel):
    changes: list[CodemodChange] = Field(default_factory=list[CodemodChange])
    skipped: list[SkippedReplacement] = Field(
        default_factory=list[SkippedReplacement],
        description="Fixes left out: conflicts, or files outside the unit",
    )
    remaining: list[Diagnostic] = Field(
        default_factory=list[Diagnostic], description="Sites the tools reported but couldn't fix"
    )
    diagnostics: list[Diagnostic] = Field(
        default_factory=list[Diagnostic], description="Compiler errors the tools ran into"
    )
    duration_s: float = Field(ge=0)
    log_paths: list[Path] = Field(default_factory=list[Path])

    @property
    def changed_files(self) -> list[str]:
        return [c.file for c in self.changes]
