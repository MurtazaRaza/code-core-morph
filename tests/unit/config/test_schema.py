from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from codecoremorph.config import manifest_json_schema
from codecoremorph.config.loader import _Yaml12Loader  # pyright: ignore[reportPrivateUsage]

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "cpp11-to-cpp20.yaml"


def _yaml(text: str) -> Any:
    return yaml.load(text, Loader=_Yaml12Loader)  # noqa: S506 - SafeLoader subclass


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    schema = manifest_json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_schema_metadata() -> None:
    schema = manifest_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"version", "project", "migration"}
    project = schema["$defs"]["ProjectConfig"]
    assert "root_path" in project["properties"]
    guardrails = schema["$defs"]["GuardrailsConfig"]["properties"]
    assert guardrails["docker_sandbox"]["deprecated"] is True
    assert guardrails["rollback_on_failure"]["const"] is True


def test_example_matches_exported_schema(validator: jsonschema.Draft202012Validator) -> None:
    validator.validate(_yaml(EXAMPLE.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "patch",
    [
        {"sandbox": {"mode": "podman"}},
        {"guardrails": {"rollback_on_failure": False}},
        {"project": {"name": "x", "root": "a", "root_path": "b"}},
        {"unknown_section": {}},
    ],
)
def test_schema_rejects_invalid_manifests(
    validator: jsonschema.Draft202012Validator, patch: dict[str, Any]
) -> None:
    data = _yaml(EXAMPLE.read_text(encoding="utf-8"))
    data.update(patch)
    assert not validator.is_valid(data)
