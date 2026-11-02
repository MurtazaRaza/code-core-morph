from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from codecoremorph.core.models import EditBatch
from codecoremorph.patching import submit_edits_schema

VALID = {
    "edits": [
        {"op": "replace", "path": "src/a.cpp", "search": "int* p", "replace": "auto p"},
        {"op": "create", "path": "src/b.h", "content": "#pragma once\n"},
        {"op": "delete", "path": "src/old.h"},
    ],
    "summary": "Converted owning pointers.",
    "review_notes": [],
}


@pytest.fixture(scope="module")
def validator() -> jsonschema.Draft202012Validator:
    schema = submit_edits_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _objects(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found += _objects(value)
    elif isinstance(node, list):
        for value in node:
            found += _objects(value)
    return found


def test_every_object_is_closed_and_fully_required() -> None:
    schema = submit_edits_schema()
    assert "$ref" not in str(schema)
    objects = _objects(schema)
    assert len(objects) == 4  # the batch and three edit kinds
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_valid_payload_passes_schema_and_model(validator: jsonschema.Draft202012Validator) -> None:
    validator.validate(VALID)
    assert len(EditBatch.model_validate(VALID).edits) == 3


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID, "extra": 1},
        {**VALID, "edits": [{"op": "rename", "path": "a", "to": "b"}]},
        {**VALID, "edits": [{"op": "delete", "path": "a", "search": "x"}]},
        {"edits": [], "summary": "s"},  # review_notes missing
    ],
)
def test_schema_rejects(
    validator: jsonschema.Draft202012Validator, payload: dict[str, Any]
) -> None:
    assert not validator.is_valid(payload)


def test_schema_is_stable() -> None:
    # Byte-stable tool definitions keep prompt caching working (CLAUDE.md rule 8).
    assert submit_edits_schema() == submit_edits_schema()
