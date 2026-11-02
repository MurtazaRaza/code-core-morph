"""JSON Schema export of the manifest, for editor completion (FR-1.7)."""

from __future__ import annotations

from typing import Any

from codecoremorph.config.models import SCHEMA_VERSION, MigrationConfig

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def manifest_json_schema() -> dict[str, Any]:
    schema = MigrationConfig.model_json_schema(mode="validation")
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        **schema,
        "description": f"CodeCoreMorph migration manifest, version {SCHEMA_VERSION} (SPEC §8)",
    }
