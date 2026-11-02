"""Manifest schema, version 1.0 (SPEC §8).

Every section rejects unknown keys, so typos are errors instead of silently ignored settings.
Two PRD spellings are accepted (SPEC §8): ``project.root_path`` for ``project.root``, and the
deprecated ``guardrails.docker_sandbox`` for ``sandbox.mode``.
"""

from __future__ import annotations

import posixpath
import re
from enum import StrEnum
from typing import Annotated, Any, cast

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

from codecoremorph.core.models.base import Role

SCHEMA_VERSION = "1.0"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"  # D-18
DEFAULT_EFFORT = "high"

Loc = tuple[str | int, ...]

_DRIVE = re.compile(r"^[A-Za-z]:")


class ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _mapping(value: object) -> dict[str, Any] | None:
    """``value`` as a mapping, for "before" validators that inspect raw input."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


# --------------------------------------------------------------------------- constrained types


def _relative_path(value: str) -> str:
    if not value:
        raise ValueError("must not be empty")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE.match(normalized):
        raise ValueError(f"{value!r} must be relative to project.root")
    if ".." in normalized.split("/"):
        raise ValueError(f"{value!r} must stay inside project.root (no '..')")
    return posixpath.normpath(normalized)


def _git_revision(value: str) -> str:
    if not value or value.startswith("-") or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError(
            f"{value!r} is not a git revision: use HEAD, a branch, a tag or a commit SHA "
            "(no spaces, and it can't start with '-')"
        )
    return value


def _pinned_image(value: str) -> str:
    if not re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", value):
        raise ValueError(
            f"{value!r} must be pinned by digest, e.g. registry/codecoremorph-cpp@sha256:<64 hex>"
        )
    return value


def _memory_size(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", value):
        raise ValueError(f"{value!r} is not a memory size; use a Docker size such as 8g or 512m")
    return value


_IDENTIFIER = r"^[a-z][a-z0-9_]*$"
_PROVIDER_NAME = r"^[a-z][a-z0-9_-]*$"
_STANDARD = r"^[a-z][a-z0-9_.+-]*$"

RelPath = Annotated[str, AfterValidator(_relative_path)]
GitRevision = Annotated[str, AfterValidator(_git_revision)]
Identifier = Annotated[str, Field(pattern=_IDENTIFIER)]
ProviderName = Annotated[str, Field(pattern=_PROVIDER_NAME)]
LanguageStandard = Annotated[str, Field(pattern=_STANDARD)]

# Plain-language versions of the patterns above, for error messages.
PATTERN_DESCRIPTIONS = {
    _IDENTIFIER: "must be a lowercase identifier (letters, digits and _), e.g. cpp or "
    "replace_typedefs_with_using",
    _PROVIDER_NAME: "must be a lowercase provider name, e.g. anthropic or ollama",
    _STANDARD: "must be a lowercase language standard, e.g. cpp11, cpp20 or java21",
}


# --------------------------------------------------------------------------- enums


class SandboxMode(StrEnum):
    AUTO = "auto"
    DOCKER = "docker"
    NATIVE = "native"


class NetworkPolicy(StrEnum):
    OFF = "off"
    FETCH_ONLY = "fetch_only"
    ON = "on"


class NewWarningsPolicy(StrEnum):
    OFF = "off"
    WARN = "warn"
    FAIL = "fail"


class TestFileEditsPolicy(StrEnum):
    __test__ = False  # not a pytest test class

    DENY = "deny"
    REVIEW = "review"
    ALLOW = "allow"


class PlanApproval(StrEnum):
    REQUIRED = "required"
    AUTO = "auto"


class UnitApproval(StrEnum):
    NONE = "none"
    API_CHANGES = "api_changes"
    TEST_CHANGES = "test_changes"
    ALL = "all"


class CharacterizationScope(StrEnum):
    UNTESTED_ONLY = "untested_only"
    ALL = "all"


# --------------------------------------------------------------------------- sections


def _project_schema_extra(schema: dict[str, Any]) -> None:
    properties = cast(dict[str, Any], schema.setdefault("properties", {}))
    properties["root_path"] = {
        "type": "string",
        "description": "Alias of `root`, accepted for compatibility with the original PRD.",
    }
    schema["not"] = {"required": ["root", "root_path"]}


class ProjectConfig(ConfigModel):
    model_config = ConfigDict(json_schema_extra=_project_schema_extra)

    name: str = Field(min_length=1)
    root: str = Field(
        default=".",
        min_length=1,
        validation_alias=AliasChoices("root", "root_path"),
        description="Git repository root, relative to this file; other paths are relative to it",
    )
    source_dirs: list[RelPath] = Field(default_factory=lambda: ["."])
    test_dirs: list[RelPath] = Field(default_factory=list[str])
    exclude: list[RelPath] = Field(
        default_factory=list[str], description="Globs never edited (third-party, generated)"
    )
    public_api: list[RelPath] = Field(
        default_factory=list[str], description="Globs whose declarations are public API"
    )

    @model_validator(mode="before")
    @classmethod
    def _single_root(cls, data: Any) -> Any:
        raw = _mapping(data)
        if raw is not None and "root" in raw and "root_path" in raw:
            raise ValueError("set `root` or its alias `root_path`, not both")
        return data


class SourceSpec(ConfigModel):
    language: Identifier
    standard: LanguageStandard
    build_system: Identifier | None = None


class TargetSpec(ConfigModel):
    language: Identifier
    standard: LanguageStandard
    rules: list[Identifier] = Field(
        default_factory=list[str], description="Applied in this order, after the standard bump"
    )

    @field_validator("rules")
    @classmethod
    def _unique_rules(cls, rules: list[str]) -> list[str]:
        duplicates = sorted({r for r in rules if rules.count(r) > 1})
        if duplicates:
            raise ValueError(f"rules listed more than once: {', '.join(duplicates)}")
        return rules


class MigrationSection(ConfigModel):
    base: GitRevision = Field(default="HEAD", description="Commit to start from")
    source: SourceSpec
    target: TargetSpec
    rules_paths: list[RelPath] = Field(
        default_factory=list[str], description="Extra project-local rule directories"
    )


class BuildToolOptions(ConfigModel):
    configure_args: list[str] = Field(default_factory=list[str])
    build_targets: list[str] = Field(default_factory=list[str], description="Empty means all")


class ModelPrice(ConfigModel):
    input_usd_per_mtok: NonNegativeFloat
    output_usd_per_mtok: NonNegativeFloat
    cache_read_usd_per_mtok: NonNegativeFloat | None = None
    cache_write_usd_per_mtok: NonNegativeFloat | None = None


class ProviderConfig(ConfigModel):
    """Connection settings for one provider. Credentials never go here (FR-1.6)."""

    base_url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    max_retries: NonNegativeInt | None = None
    timeout_s: PositiveFloat | None = None
    prices: dict[str, ModelPrice] = Field(
        default_factory=dict[str, ModelPrice], description="Per-model price overrides"
    )


class RoleModelConfig(ConfigModel):
    """Which model serves a role. There is deliberately no universal temperature setting."""

    provider: ProviderName
    model: str = Field(min_length=1)
    effort: str | None = Field(
        default=None, pattern=r"^[a-z]+$", description="Anthropic: reasoning effort"
    )
    num_ctx: PositiveInt | None = Field(
        default=None, description="Ollama: context window, always sent explicitly"
    )
    provider_options: dict[str, JsonValue] = Field(
        default_factory=dict[str, JsonValue],
        description="Other provider-specific options, passed through unchanged",
    )


def _default_roles() -> dict[Role, RoleModelConfig]:
    return {
        Role.EDITOR: RoleModelConfig(
            provider=DEFAULT_PROVIDER, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT
        )
    }


class EscalationConfig(ConfigModel):
    after_failed_attempts: PositiveInt
    to: RoleModelConfig


class LlmConfig(ConfigModel):
    providers: dict[ProviderName, ProviderConfig] = Field(default_factory=dict[str, ProviderConfig])
    roles: dict[Role, RoleModelConfig] = Field(
        default_factory=_default_roles, description="Unset roles fall back to `editor`"
    )
    escalation: EscalationConfig | None = None
    anthropic_refusal_fallback: bool = True

    @field_validator("roles")
    @classmethod
    def _editor_required(cls, roles: dict[Role, RoleModelConfig]) -> dict[Role, RoleModelConfig]:
        if Role.EDITOR not in roles:
            raise ValueError("`editor` is required, because unset roles fall back to it")
        return roles

    def for_role(self, role: Role) -> RoleModelConfig:
        return self.roles.get(role) or self.roles[Role.EDITOR]


class GuardrailsConfig(ConfigModel):
    max_retry_budget: NonNegativeInt = Field(default=3, description="Retries after the first")
    max_edit_retries: NonNegativeInt = 2
    max_run_cost_usd: PositiveFloat | None = Field(
        default=50.0, description="Paid providers only; null means no cap"
    )
    max_run_tokens: PositiveInt | None = None
    max_unit_minutes: PositiveInt = 30
    max_unit_diff_lines: PositiveInt = 800
    build_timeout_s: PositiveInt = 900
    test_timeout_s: PositiveInt = 1800
    require_passing_tests: bool = True
    rollback_on_failure: bool = Field(default=True, json_schema_extra={"const": True})
    new_warnings: NewWarningsPolicy = NewWarningsPolicy.WARN
    test_file_edits: TestFileEditsPolicy = TestFileEditsPolicy.REVIEW
    docker_sandbox: bool | None = Field(
        default=None,
        description="Deprecated alias: true means sandbox.mode docker, false means native",
        json_schema_extra={"deprecated": True},
    )

    @field_validator("rollback_on_failure")
    @classmethod
    def _rollback_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "must be true: failed units are always rolled back, so the migration branch "
                "stays green (NFR-2.2)"
            )
        return value


class SandboxConfig(ConfigModel):
    mode: SandboxMode = SandboxMode.AUTO
    image: Annotated[str, AfterValidator(_pinned_image)] | None = Field(
        default=None, description="Image override; must be pinned by digest"
    )
    network: NetworkPolicy = NetworkPolicy.FETCH_ONLY
    cpus: PositiveFloat = 4
    memory: Annotated[str, AfterValidator(_memory_size)] = "8g"
    pids_limit: PositiveInt = Field(default=4096, description="Max processes in the container")


class ApprovalsConfig(ConfigModel):
    plan: PlanApproval = PlanApproval.REQUIRED
    unit: UnitApproval = UnitApproval.NONE


class CharacterizationTestsConfig(ConfigModel):
    enabled: bool = True
    scope: CharacterizationScope = CharacterizationScope.UNTESTED_ONLY
    dir: RelPath = "tests/codecoremorph_characterization"
    max_tests_per_unit: PositiveInt = 10
    keep: bool = True


class ParityConfig(ConfigModel):
    baseline_runs: PositiveInt = 2
    characterization_tests: CharacterizationTestsConfig = Field(
        default_factory=CharacterizationTestsConfig
    )


class ReportingConfig(ConfigModel):
    store_transcripts: bool = True


def _git_author(value: str) -> str:
    if not re.fullmatch(r"[^<>\n]+ <[^<>\s]+>", value):
        raise ValueError(f"{value!r} must look like 'Name <email>'")
    return value


class GitConfig(ConfigModel):
    author: Annotated[str, AfterValidator(_git_author)] | None = Field(
        default=None,
        description="Author of unit commits, 'Name <email>'; default: your git identity",
    )


class MigrationConfig(ConfigModel):
    """The whole manifest (``migration_config.yaml``)."""

    model_config = ConfigDict(title="CodeCoreMorph migration manifest")

    version: str = Field(json_schema_extra={"enum": [SCHEMA_VERSION]})
    project: ProjectConfig
    migration: MigrationSection
    build: dict[Identifier, BuildToolOptions] = Field(
        default_factory=dict[str, BuildToolOptions],
        description="Build-system options, keyed by build system (e.g. cmake)",
    )
    llm: LlmConfig = Field(default_factory=LlmConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    parity: ParityConfig = Field(default_factory=ParityConfig)
    reporting: ReportingConfig = Field(default_factory=ReportingConfig)
    git: GitConfig = Field(default_factory=GitConfig)

    @field_validator("version", mode="before")
    @classmethod
    def _version_as_text(cls, value: Any) -> Any:
        # `version: 1.0` without quotes is a YAML float.
        if isinstance(value, int | float) and not isinstance(value, bool):
            return f"{float(value):.1f}"
        return value

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported manifest version {value!r}; "
                f"this CodeCoreMorph reads version {SCHEMA_VERSION}"
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _apply_docker_sandbox_alias(cls, data: Any) -> Any:
        # Map the deprecated flag onto sandbox.mode when mode isn't set. The loader reports the
        # deprecation, and any conflict between the two keys, with their file positions.
        raw = _mapping(data)
        guardrails = _mapping(raw.get("guardrails")) if raw is not None else None
        if raw is None or guardrails is None:
            return data
        flag = guardrails.get("docker_sandbox")
        sandbox = _mapping(raw.get("sandbox") or {})
        if not isinstance(flag, bool) or sandbox is None or "mode" in sandbox:
            return data
        mode = SandboxMode.DOCKER if flag else SandboxMode.NATIVE
        return {**raw, "sandbox": {**sandbox, "mode": mode.value}}
