"""Migration manifest: schema (pydantic), loader and JSON Schema export (SPEC §8)."""

from codecoremorph.config.issues import ConfigIssue, ConfigValidationError, IssueSeverity
from codecoremorph.config.loader import (
    DEFAULT_CONFIG_NAME,
    LoadedConfig,
    config_hash,
    load_config,
    load_yaml12,
    locate_issues,
    parse_config,
)
from codecoremorph.config.models import SCHEMA_VERSION, MigrationConfig
from codecoremorph.config.schema import manifest_json_schema

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "SCHEMA_VERSION",
    "ConfigIssue",
    "ConfigValidationError",
    "IssueSeverity",
    "LoadedConfig",
    "MigrationConfig",
    "config_hash",
    "load_config",
    "load_yaml12",
    "locate_issues",
    "manifest_json_schema",
    "parse_config",
]
