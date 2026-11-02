"""Edit application (PatchEngine) and the ``submit_edits`` schema (SPEC §7.4)."""

from codecoremorph.patching.engine import PatchEngine, PatchError
from codecoremorph.patching.schema import SUBMIT_EDITS_TOOL, submit_edits_schema
from codecoremorph.patching.textfile import NormalizedView, TextFile

__all__ = [
    "SUBMIT_EDITS_TOOL",
    "NormalizedView",
    "PatchEngine",
    "PatchError",
    "TextFile",
    "submit_edits_schema",
]
