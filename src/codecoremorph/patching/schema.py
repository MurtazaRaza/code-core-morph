"""JSON Schema of the agent's ``submit_edits`` tool (SPEC §7.4).

Written by hand rather than generated: it stays inline (no ``$ref``), closes every object
(``additionalProperties: false``) and marks every property required, which keeps it usable
with strict tool-input validation. Phase 4 checks it against each provider's strict mode.
Descriptions are part of the prompt; keep them stable so prompt caching keeps working.
"""

from __future__ import annotations

from typing import Any

SUBMIT_EDITS_TOOL = "submit_edits"

_PATH = {
    "type": "string",
    "description": "File path relative to the repository root, with forward slashes.",
}


def _edit(op: str, description: str, fields: dict[str, dict[str, str]]) -> dict[str, Any]:
    properties: dict[str, Any] = {"op": {"type": "string", "enum": [op]}, "path": _PATH, **fields}
    return {
        "type": "object",
        "description": description,
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def submit_edits_schema() -> dict[str, Any]:
    replace = _edit(
        "replace",
        "Replace one exact, unique occurrence of `search` in an existing file with `replace`.",
        {
            "search": {
                "type": "string",
                "description": "Exact text from the current file. It must occur exactly once; "
                "include surrounding lines to make it unique.",
            },
            "replace": {"type": "string", "description": "The new text."},
        },
    )
    create = _edit(
        "create",
        "Create a new file. Fails if the file exists.",
        {"content": {"type": "string", "description": "The complete file contents."}},
    )
    delete = _edit("delete", "Delete an existing file.", {})
    return {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "description": "Edits applied in order, all or nothing.",
                "items": {"anyOf": [replace, create, delete]},
            },
            "summary": {
                "type": "string",
                "description": "What changed and why, for the commit message.",
            },
            "review_notes": {
                "type": "array",
                "description": "Anything a human reviewer should check. May be empty.",
                "items": {"type": "string"},
            },
        },
        "required": ["edits", "summary", "review_notes"],
        "additionalProperties": False,
    }
