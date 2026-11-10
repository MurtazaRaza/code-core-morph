"""Rules: named transformations with detectors, codemods, LLM guidance and post-conditions
(SPEC §7.3). A rule is YAML plus Markdown; no Python is needed (NFR-3.2)."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from codecoremorph.core.models.base import CoreModel

RULE_ID_PATTERN = r"^[a-z][a-z0-9_]*$"


class RuleKind(StrEnum):
    API_PRESERVING = "api_preserving"
    API_CHANGING = "api_changing"  # the planner expands units by fan-in (FR-5.3)
    PROJECT = "project"  # build-configuration changes such as the standard bump
    # Replaces code with code in the target language behind the same interface (D-56): one
    # unit per interface, with the build files, and the new code's files (``extra_scope``).
    PORT = "port"


class StandardRange(CoreModel):
    """Which migrations something applies to, by language standard. Standards compare in the
    order the language adapter lists them (``AdapterCapabilities.standards``)."""

    min_target_standard: str | None = Field(
        default=None, description="Only when the target standard is this one or newer"
    )
    max_source_standard: str | None = Field(
        default=None, description="Only when the source standard is this one or older"
    )

    def standards(self) -> list[str]:
        return [s for s in (self.min_target_standard, self.max_source_standard) if s is not None]


class AppliesTo(StandardRange):
    standard_bump: bool = Field(
        default=False,
        description="Only when the target standard is newer than the source (the standard bump)",
    )


class PatternSpec(StandardRange):
    """A detector pattern that only counts for some migrations, e.g. a feature removed in
    C++17 is a site only when the target is C++17 or newer."""

    pattern: str = Field(min_length=1)


class DetectorType(StrEnum):
    PATTERN = "pattern"  # regular expressions over code, with comments and strings masked
    ADAPTER_QUERY = "adapter_query"  # a named query the language adapter answers
    HOOK = "hook"  # a Python function, "module:function"
    # Every file of the rule's language is a site until it is deleted: a port that replaces
    # its files, an executable's or the retirement's (D-60).
    FILES = "files"


class Detector(CoreModel):
    """Finds the sites a rule applies to. Files without sites are skipped."""

    type: DetectorType
    patterns: list[str | PatternSpec] = Field(
        default_factory=list[str | PatternSpec],
        description="For type: pattern. A plain string always applies.",
    )
    query: str | None = Field(default=None, description="For type: adapter_query")
    hook: str | None = Field(default=None, pattern=r"^[\w.]+:\w+$", description="For type: hook")

    @model_validator(mode="after")
    def _fields_match_type(self) -> Detector:
        needed = {
            DetectorType.PATTERN: ("patterns", bool(self.patterns)),
            DetectorType.ADAPTER_QUERY: ("query", self.query is not None),
            DetectorType.HOOK: ("hook", self.hook is not None),
            DetectorType.FILES: ("type", True),
        }[self.type]
        if not needed[1]:
            raise ValueError(f"a {self.type.value} detector needs `{needed[0]}`")
        for spec in self.pattern_specs():
            try:
                re.compile(spec.pattern)
            except re.error as exc:
                raise ValueError(f"invalid pattern {spec.pattern!r}: {exc}") from None
        return self

    def pattern_specs(self) -> list[PatternSpec]:
        return [PatternSpec(pattern=p) if isinstance(p, str) else p for p in self.patterns]


class CodemodSpec(CoreModel):
    """One deterministic tool pass, e.g. clang-tidy with a set of checks."""

    tool: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    checks: list[str] = Field(default_factory=list[str])
    options: dict[str, str] = Field(default_factory=dict[str, str])
    standard: str | None = Field(
        default=None,
        description="Parse under at least this standard instead of the build's; the pass is "
        "skipped when the migration's target is older. A standard bump uses it to replace "
        "removed features while they still exist next to their replacements.",
    )


class LlmGuidance(CoreModel):
    guidance: str | None = Field(default=None, description="Markdown file, relative to the rule")
    examples: str | None = Field(default=None, description="Directory, relative to the rule")


class PostconditionType(StrEnum):
    DETECTOR_EMPTY = "detector_empty"  # the rule's detector finds no sites in the unit's files
    BUILDS_CLEANLY = "builds_cleanly"  # the unit builds with no errors under the target standard
    CODEMOD_CLEAN = "codemod_clean"  # the codemod checks report no remaining sites
    HOOK = "hook"
    # A port unit's new code exports the C API its kept headers declare, with the same
    # signatures (D-56); port rules always check it.
    C_ABI_KEPT = "c_abi_kept"
    # Module ports (D-58): unsafe code only in the bridge; the target's linter quiet on the
    # unit's new code; the symbol map whole for the unit's headers.
    UNSAFE_IN_BRIDGE = "unsafe_in_bridge"
    LINT_CLEAN = "lint_clean"
    SYMBOL_MAP = "symbol_map"
    # Tests (D-60): every test the unit translates is in the project's test map, each mapped
    # target test run and passing.
    TEST_MAP = "test_map"


class Postcondition(CoreModel):
    type: PostconditionType
    severity: Literal["error", "warning"] = "error"
    hook: str | None = Field(default=None, pattern=r"^[\w.]+:\w+$")


class SkipCondition(StrEnum):
    C_COMPATIBLE = "c_compatible"  # headers shared with C (FR-2.3)
    PUBLIC_API = "public_api"
    TEST = "test"


class PortUnits(StrEnum):
    """How a port rule cuts its units (D-58)."""

    # One unit per C interface: the sources defining what a header declares with C linkage,
    # the header kept as it is (Phase 12, D-57).
    C_HEADER = "c_header"
    # One unit per module (a cluster of the file graph a library target compiles), leaves
    # first, its headers kept as façades over the new code (Phase 13).
    MODULE = "module"
    # Phase 14 (D-60): one unit per executable that isn't a test, its sources replaced by a
    # program in the target language; one per test source, translated beside it; and one last
    # unit that retires what is left of the source language.
    EXECUTABLE = "executable"
    TESTS = "tests"
    RETIREMENT = "retirement"
    # Phase 16 (D-75): one unit per module file of a Rust crate, leaves first by the
    # references between them, the module kept as a Rust façade over the C that takes it over.
    RUST_MODULE = "rust_module"
    # C (D-68): one unit per source file of a library, ordered leaves first by the calls
    # between them; the headers stay as they are, and the new code exports the functions.
    TRANSLATION_UNIT = "translation_unit"
    # Phase 17 (D-78): one unit per type of a C# library, leaves first by the references
    # between them. A reference cycle is one unit, and so is a class with virtual members with
    # the project's classes that derive from it; nested types go with their outer type. Each
    # type keeps its C# API as a façade over the code that takes it over.
    CSHARP_TYPE = "csharp_type"


class Rule(CoreModel):
    id: str = Field(pattern=RULE_ID_PATTERN)
    language: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: RuleKind
    description: str = Field(min_length=1)
    applies_to: AppliesTo = Field(default_factory=AppliesTo)
    detector: Detector | None = None
    codemods: list[CodemodSpec] = Field(default_factory=list[CodemodSpec])
    llm: LlmGuidance | None = None
    postconditions: list[Postcondition] = Field(default_factory=list[Postcondition])
    skip: list[SkipCondition] = Field(default_factory=list[SkipCondition])
    allow_suppressions: bool | list[str] = Field(
        default=False,
        description="Whether the rule may add NOLINT, #[allow(...)] and similar (I-7): true, "
        "false, or globs of the files where it may (a module port: its bridge, D-58)",
    )
    target_language: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="For kind: port, the language the code is ported to (migration.target)",
    )
    extra_scope: list[str] = Field(
        default_factory=list[str],
        description="For kind: port, globs of files a unit may also create or edit, e.g. "
        "rust/** for the crates",
    )
    port_units: PortUnits | None = Field(
        default=None,
        description="For kind: port, how units are cut: c_header (one per C interface, the "
        "default), module (one per module, behind façades), translation_unit (one per C "
        "source file of a library, by calls), executable (one per program), tests (one per "
        "test source) or retirement (one, last)",
    )

    @model_validator(mode="after")
    def _port_fields(self) -> Rule:
        if self.kind is RuleKind.PORT and self.target_language is None:
            raise ValueError("a port rule needs `target_language`")
        if self.kind is not RuleKind.PORT and (
            self.target_language or self.extra_scope or self.port_units is not None
        ):
            raise ValueError(
                "`target_language`, `extra_scope` and `port_units` are for port rules only"
            )
        if self.target_language is not None and self.target_language == self.language:
            raise ValueError("a port rule's target_language differs from its language")
        if self.kind is RuleKind.PORT and (
            self.applies_to.standard_bump or self.applies_to.min_target_standard is not None
        ):
            raise ValueError(
                "a port rule's applies_to names source standards only (max_source_standard)"
            )
        if self.kind is RuleKind.PORT and (
            any(c.standard is not None for c in self.codemods)
            or any(
                s.min_target_standard is not None
                for s in (self.detector.pattern_specs() if self.detector else [])
            )
        ):
            raise ValueError("a port rule's codemods and patterns name no target standard")
        globs = self.allow_suppressions if isinstance(self.allow_suppressions, list) else []
        for glob in [*self.extra_scope, *globs]:
            if glob.startswith("/") or ".." in glob.split("/"):
                raise ValueError(f"{glob!r} must stay inside the project")
        return self

    @property
    def module_port(self) -> bool:
        """A port cut by module, behind façades (D-58)."""
        return self.kind is RuleKind.PORT and self.port_units is PortUnits.MODULE

    @property
    def retirement(self) -> bool:
        """The port's last unit, which removes the source language's code (D-60)."""
        return self.kind is RuleKind.PORT and self.port_units is PortUnits.RETIREMENT

    @property
    def natural_language_only(self) -> bool:
        """Only LLM guidance: applies to every in-scope file (FR-9.3)."""
        return self.detector is None and not self.codemods and self.llm is not None


class LoadedRule(CoreModel):
    """A rule with where it came from, so guidance paths resolve."""

    rule: Rule
    pack: str
    source: Path = Field(description="The rule's YAML file")

    @property
    def base_dir(self) -> Path:
        return self.source.parent

    def guidance_path(self) -> Path | None:
        if self.rule.llm is None or self.rule.llm.guidance is None:
            return None
        return self.base_dir / self.rule.llm.guidance


class RuleSite(CoreModel):
    """One place a detector matched."""

    file: str
    line: int = Field(ge=1)
    column: int = Field(default=1, ge=1)
    text: str = ""
