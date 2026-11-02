"""PatchEngine and git agree: the generated diff, reverse-applied, restores the original bytes.

This is the Phase 1 property test from the plan: apply an edit with PatchEngine, take the
diff CodeCoreMorph generates (FR-3.2), apply it in reverse with ``git apply -R``, and get the
original file back byte for byte, whatever its line endings and BOM.
"""

from __future__ import annotations

import codecs
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from codecoremorph.core.models import EditBatch
from codecoremorph.patching import PatchEngine
from codecoremorph.vcs import Repository, Worktree

RUN = "20261102-141500-a3f9"
MARKER = "@@TARGET@@"

_text = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters="\r\n\x00@"), max_size=10
)
_lines = st.lists(st.tuples(_text, st.sampled_from(["\n", "\r\n"])), max_size=6)


@pytest.fixture
def worktree(repo: Path, tmp_path: Path) -> Worktree:
    repository = Repository(repo)
    return repository.add_worktree(
        tmp_path / "wt" / RUN, f"codecoremorph/{RUN}", repository.resolve_commit("HEAD")
    )


@settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(head=_lines, tail=_lines, bom=st.booleans(), new=_text)
def test_reverse_diff_restores_original_bytes(
    worktree: Worktree,
    head: list[tuple[str, str]],
    tail: list[tuple[str, str]],
    bom: bool,
    new: str,
) -> None:
    target = worktree.path / "src" / "subject.txt"
    original = (codecs.BOM_UTF8 if bom else b"") + (
        "".join(t + n for t, n in head) + MARKER + "".join(t + n for t, n in tail)
    ).encode()
    target.write_bytes(original)
    # An example can repeat the previous one's content; then HEAD already is the base.
    base = worktree.commit("base for this example") if worktree.has_changes() else worktree.head()
    try:
        result = PatchEngine(worktree.path).apply(
            EditBatch.model_validate(
                {
                    "edits": [
                        {
                            "op": "replace",
                            "path": "src/subject.txt",
                            "search": MARKER,
                            "replace": new,
                        },
                        {"op": "create", "path": "src/created.txt", "content": "new\nfile\n"},
                    ],
                    "summary": "s",
                }
            )
        )
        assert result.applied, result.feedback()
        patch = worktree.diff_bytes(base)
        subprocess.run(
            ["git", "apply", "-R", "--index", "--whitespace=nowarn", "-"],
            cwd=worktree.path,
            input=patch,
            check=True,
            capture_output=True,
        )
        assert target.read_bytes() == original
        assert not (worktree.path / "src" / "created.txt").exists()
    finally:
        worktree.rollback(base)
