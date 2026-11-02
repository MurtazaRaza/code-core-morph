from __future__ import annotations

import pytest

from codecoremorph.vcs import parse_trailers, summarize_paths, unit_commit_message
from codecoremorph.vcs.messages import with_trailers


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], "no files"),
        (["src/a.cpp"], "src/a.cpp"),
        (["src/geometry/vector.h", "src/geometry/vector.cpp"], "src/geometry/vector.{cpp,h}"),
        (["b.cpp", "a.cpp"], "a.cpp, b.cpp"),
        (["src/x/a.h", "src/x/b.h", "src/y/c.h", "src/y/d.h"], "4 files in src/"),
        (["a.h", "b.h", "c.h", "d.h"], "4 files"),
    ],
)
def test_summarize_paths(paths: list[str], expected: str) -> None:
    assert summarize_paths(paths) == expected


def test_unit_commit_message_matches_the_spec_format() -> None:
    message = unit_commit_message(
        run_id="20261102-141500-a3f9",
        unit_id="U-0042",
        rules=["replace_typedefs_with_using"],
        files=["src/geometry/vector.h", "src/geometry/vector.cpp"],
        summary="Replaced 12 typedef declarations with alias declarations.",
        attempts=2,
        models=["anthropic/claude-opus-5-5"],
    )
    assert message == (
        "codecoremorph(replace_typedefs_with_using): src/geometry/vector.{cpp,h}\n"
        "\n"
        "Replaced 12 typedef declarations with alias declarations.\n"
        "\n"
        "CodeCoreMorph-Run: 20261102-141500-a3f9\n"
        "CodeCoreMorph-Unit: U-0042\n"
        "CodeCoreMorph-Rules: replace_typedefs_with_using\n"
        "CodeCoreMorph-Attempts: 2\n"
        "CodeCoreMorph-Models: anthropic/claude-opus-5-5\n"
    )
    assert parse_trailers(message)["CodeCoreMorph-Unit"] == "U-0042"


def test_codemod_only_units_name_no_model() -> None:
    message = unit_commit_message(
        run_id="20261102-141500-a3f9",
        unit_id="U-0001",
        rules=["a", "b"],
        files=["x.h"],
        summary="",
        attempts=1,
        models=[],
    )
    assert message.startswith("codecoremorph(a,b): x.h\n\nCodeCoreMorph-Run:")
    assert parse_trailers(message)["CodeCoreMorph-Models"] == "none"


def test_trailers_are_validated() -> None:
    with pytest.raises(ValueError, match="invalid commit trailer"):
        with_trailers("subject", {"Bad Key": "x"})
    with pytest.raises(ValueError, match="invalid commit trailer"):
        with_trailers("subject", {"Key": "multi\nline"})
    assert parse_trailers("subject only") == {}
    assert parse_trailers("subject\n\nbody text, not trailers") == {}
