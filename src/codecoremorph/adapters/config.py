"""The slice of a manifest an adapter receives (:class:`LanguageConfig`)."""

from __future__ import annotations

from codecoremorph.config.models import BuildToolOptions, MigrationConfig
from codecoremorph.core.models import LanguageConfig

DEFAULT_BUILD_SYSTEMS = {
    "c": "make",
    "cpp": "cmake",
    "rust": "cargo",
    "java": "maven",
    "csharp": "dotnet",
}


def is_port(config: MigrationConfig) -> bool:
    """A migration to another language (D-56): a pair of adapters, source and target."""
    return config.migration.target.language != config.migration.source.language


def target_language_config(config: MigrationConfig) -> LanguageConfig:
    """The target adapter's slice of a port's manifest: the target language and standard,
    with the project's layout and the guardrails' timeouts."""
    target = config.migration.target
    project = config.project
    return LanguageConfig(
        language=target.language,
        standard=target.standard,
        target_standard=target.standard,
        build_system=DEFAULT_BUILD_SYSTEMS.get(target.language),
        source_dirs=list(project.source_dirs),
        test_dirs=config.test_dirs,
        exclude=list(project.exclude),
        build_timeout_s=config.guardrails.build_timeout_s,
        test_timeout_s=config.guardrails.test_timeout_s,
    )


def language_config(config: MigrationConfig, *, standard: str | None = None) -> LanguageConfig:
    """``standard`` overrides the standard to build with (default: the source standard)."""
    source = config.migration.source
    target = config.migration.target
    build_system = source.build_system or DEFAULT_BUILD_SYSTEMS.get(source.language)
    options = config.build.get(build_system or "", BuildToolOptions())
    project = config.project
    return LanguageConfig(
        language=source.language,
        standard=standard or source.standard,
        # A port's target standard is the other language's; the source keeps its own.
        target_standard=None if target.language != source.language else target.standard,
        build_system=build_system,
        configure_args=list(options.configure_args),
        build_targets=list(options.build_targets),
        test_targets=list(options.test_targets),
        source_dirs=list(project.source_dirs),
        test_dirs=config.test_dirs,
        exclude=list(project.exclude),
        public_api=list(project.public_api),
        build_timeout_s=config.guardrails.build_timeout_s,
        test_timeout_s=config.guardrails.test_timeout_s,
    )
