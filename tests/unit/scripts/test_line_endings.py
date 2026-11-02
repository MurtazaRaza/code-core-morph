"""Scripts keep the line endings their interpreters need (.gitattributes enforces them in git)."""

from __future__ import annotations

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


@pytest.mark.parametrize("path", sorted(SCRIPTS.rglob("*.bat")), ids=lambda p: p.name)
def test_batch_files_are_crlf(path: Path) -> None:
    data = path.read_bytes()
    # cmd.exe misreads labels in LF-only batch files.
    assert data.count(b"\n") == data.count(b"\r\n"), f"{path} must use CRLF line endings"


@pytest.mark.parametrize(
    "path",
    [*sorted(SCRIPTS.rglob("*.sh")), SCRIPTS / "deps" / "manifest.txt"],
    ids=lambda p: p.name,
)
def test_shell_files_are_lf(path: Path) -> None:
    assert b"\r" not in path.read_bytes(), f"{path} must use LF line endings"


def test_every_script_has_a_twin() -> None:
    bat = {p.relative_to(SCRIPTS).with_suffix("") for p in SCRIPTS.rglob("*.bat")}
    sh = {p.relative_to(SCRIPTS).with_suffix("") for p in SCRIPTS.rglob("*.sh")}
    assert bat == sh, "every .bat script needs a .sh twin and vice versa"
