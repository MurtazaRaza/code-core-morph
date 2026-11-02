from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.core.paths import HomeLayout, codecoremorph_home


def test_absolute_override_wins(tmp_path: Path) -> None:
    env = {"CODECOREMORPH_HOME": str(tmp_path / "x"), "LOCALAPPDATA": "C:\\ignored"}
    assert codecoremorph_home(env, platform="win32") == tmp_path / "x"


def test_relative_override_resolves_against_cwd(tmp_path: Path) -> None:
    home = codecoremorph_home({"CODECOREMORPH_HOME": "rel/dir"}, platform="linux", cwd=tmp_path)
    assert home == tmp_path / "rel" / "dir"


def test_blank_override_is_ignored() -> None:
    env = {"CODECOREMORPH_HOME": "  ", "HOME": "/home/u"}
    assert codecoremorph_home(env, platform="linux") == Path("/home/u/.cache/codecoremorph")


def test_windows_default() -> None:
    env = {"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"}
    assert codecoremorph_home(env, platform="win32") == Path(
        "C:\\Users\\u\\AppData\\Local\\codecoremorph"
    )


def test_windows_without_localappdata() -> None:
    env = {"USERPROFILE": "C:\\Users\\u"}
    assert codecoremorph_home(env, platform="win32") == Path(
        "C:\\Users\\u\\AppData\\Local\\codecoremorph"
    )


def test_macos_default() -> None:
    home = codecoremorph_home({"HOME": "/Users/u"}, platform="darwin")
    assert home == Path("/Users/u/Library/Caches/codecoremorph")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"HOME": "/home/u"}, "/home/u/.cache/codecoremorph"),
        ({"HOME": "/home/u", "XDG_CACHE_HOME": "/xdg"}, "/xdg/codecoremorph"),
        # The XDG spec says relative values are ignored.
        ({"HOME": "/home/u", "XDG_CACHE_HOME": "rel"}, "/home/u/.cache/codecoremorph"),
    ],
)
def test_linux_defaults(env: dict[str, str], expected: str) -> None:
    assert codecoremorph_home(env, platform="linux") == Path(expected)


def test_layout(tmp_path: Path) -> None:
    layout = HomeLayout(tmp_path)
    run_id = "20261102-141500-a3f9"
    assert layout.worktree(run_id) == tmp_path / "worktrees" / run_id
    assert layout.build_dir(run_id) == tmp_path / "build" / run_id
    assert layout.toolchain_env("cpp") == tmp_path / "toolchains" / "cpp.env"
    assert layout.downloads == tmp_path / "downloads"
    with pytest.raises(ValueError, match="invalid run ID"):
        layout.worktree("../../etc")


def test_layout_resolve_uses_environment(isolated_home: Path) -> None:
    assert HomeLayout.resolve().root == isolated_home
