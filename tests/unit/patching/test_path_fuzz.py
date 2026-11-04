"""Path traversal fuzzing (security review, Phase 8; SPEC §7.4 rule 4, §10).

Edits with paths built from adversarial pieces go to a real PatchEngine. Whatever the path,
nothing outside the worktree, inside ``.git`` or in an excluded directory may change; the
model's validation either accepts a path or raises ``ValidationError``, nothing else.
"""

from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from codecoremorph.core.models import CreateEdit, DeleteEdit, EditBatch, ReplaceEdit
from codecoremorph.patching import PatchEngine

TWO_DOTS = chr(0x2025)  # TWO DOT LEADER, which looks like ".."
FULLWIDTH_DOTS = chr(0xFF0E) * 2  # FULLWIDTH FULL STOP, twice
PIECES = [
    "src", "a.h", "new.h", "..", ".", "", ".git", ".GIT", ".git ", ".git.", "GIT~1", "git",
    "hooks", "config", "third_party", "Third_Party", "THIRD_PARTY", "THIRD_~1", "lib.h", "~",
    "C:", "c:", "\\", "..\\..", "%2e%2e", TWO_DOTS, FULLWIDTH_DOTS, "con", "CON.h", "aux.c",
    "a ", "a.", " ", "\x00", "\t", "a:b", "a.h::$DATA", "link-out", "link-git", "\\\\?\\C:",
    "//server/share", "outside",
]  # fmt: skip
EXCLUDE = ["third_party/**"]


def _digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<dir>"
        )
        for p in sorted(root.rglob("*"))
        if not p.is_symlink()
    }


@pytest.fixture(scope="module")
def area(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``wt/`` (the worktree, with a .git and an excluded directory) beside ``outside/``."""
    base = tmp_path_factory.mktemp("fuzz")
    wt = base / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "a.h").write_text("int a;\n", encoding="utf-8")
    (wt / ".git" / "hooks").mkdir(parents=True)
    (wt / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (wt / "third_party").mkdir()
    (wt / "third_party" / "lib.h").write_text("int lib;\n", encoding="utf-8")
    (base / "outside").mkdir()
    (base / "outside" / "secret.txt").write_text("keep out\n", encoding="utf-8")
    for name, target in (("link-out", base / "outside"), ("link-git", wt / ".git")):
        # Windows without Developer Mode makes no links; the other cases still run.
        with contextlib.suppress(OSError):
            (wt / name).symlink_to(target, target_is_directory=True)
    return base


def _edit(op: str, path: str) -> Any:
    fields: dict[str, str] = {"op": op, "path": path}
    if op == "create":
        fields["content"] = "pwned\n"
    elif op == "replace":
        fields |= {"search": "int", "replace": "long"}
    try:  # what a model's submit_edits goes through
        return EditBatch.model_validate({"edits": [fields], "summary": "fuzz"}).edits[0]
    except ValidationError:
        pass
    # Bypass the model's checks, to prove the engine checks paths on its own.
    model = {"create": CreateEdit, "replace": ReplaceEdit, "delete": DeleteEdit}[op]
    return model.model_construct(None, **fields)


def _protected(rel: str) -> bool:
    """Whether a change at ``rel`` (relative to the area) breaks the path rules."""
    parts = rel.split("/")
    if parts[0] != "wt":
        return True
    inside = [p.lower() for p in parts[1:]]
    return bool(inside) and inside[0] in (".git", "third_party")


@given(
    pieces=st.lists(st.sampled_from(PIECES), min_size=1, max_size=5),
    separator=st.sampled_from(["/", "\\", "//"]),
    op=st.sampled_from(["create", "replace", "delete"]),
)
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fuzzed_paths_never_leave_the_rules(
    area: Path, pieces: list[str], separator: str, op: str
) -> None:
    path = separator.join(pieces)
    before = _digest(area)
    PatchEngine(area / "wt", exclude=EXCLUDE).apply([_edit(op, path)])
    after = _digest(area)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert [c for c in changed if _protected(c)] == [], f"edit to {path!r} changed {changed}"


@given(path=st.text(max_size=40))
@settings(max_examples=300, deadline=None)
def test_validation_never_crashes(path: str) -> None:
    edit = {"op": "create", "path": path, "content": "x"}
    with contextlib.suppress(ValidationError):
        EditBatch.model_validate({"edits": [edit], "summary": "fuzz"})
