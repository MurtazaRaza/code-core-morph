from __future__ import annotations

import pytest

from codecoremorph.core.errors import HostEnvironmentError
from codecoremorph.vcs import git as git_module
from codecoremorph.vcs import parse_git_version


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git version 2.43.0\n", (2, 43, 0)),
        ("git version 2.43.0.windows.1", (2, 43, 0)),
        ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
        ("git version 2.30", (2, 30, 0)),
    ],
)
def test_parse_git_version(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_git_version(text) == expected


def test_parse_git_version_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        parse_git_version("hg version 6")


def test_missing_git_is_an_environment_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_module.shutil, "which", lambda name: None)
    with pytest.raises(HostEnvironmentError, match="not found") as info:
        git_module.git_version()
    assert info.value.hint is not None


def test_installed_git_is_recent_enough() -> None:
    # git is a prerequisite of the development setup; `--version` touches no repository.
    assert git_module.git_version()[:2] >= git_module.MIN_GIT_VERSION
