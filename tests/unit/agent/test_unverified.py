"""Code the build doesn't compile: echoes of build errors, unverified changes (D-42)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.agent.host import HostFiles
from codecoremorph.agent.unverified import (
    echo_feedback,
    echoes,
    error_lines,
    unverified_warnings,
)
from codecoremorph.core.models import Diagnostic, DiagnosticOrigin, Severity
from codecoremorph.parity.baseline import BaselineCache
from codecoremorph.vcs.messages import parse_trailers

from .conftest import GitRunner, World, unit

POSIX = "env_initialized_.store(true, std::memory_order::memory_order_relaxed);"


def _error(path: str, line: int, severity: Severity = Severity.ERROR) -> Diagnostic:
    return Diagnostic(
        file_path=path,
        line=line,
        column=1,
        severity=severity,
        message="no member named 'memory_order_relaxed' in 'std::memory_order'",
        origin=DiagnosticOrigin.COMPILER,
    )


def _leveldb(tmp_path: Path) -> Path:
    (tmp_path / "util").mkdir()
    (tmp_path / "util" / "env_posix.cc").write_text(f"int a;\n    {POSIX}\n}}\n", encoding="utf-8")
    (tmp_path / "util" / "env_windows.cc").write_text(
        f"int b;\n\n  {POSIX}\n}}\n", encoding="utf-8"
    )
    return tmp_path


def test_build_errors_repeat_in_files_the_build_never_compiles(tmp_path: Path) -> None:
    """Regression: the bump fixed env_posix.cc on Linux and left env_windows.cc broken."""
    root = _leveldb(tmp_path)
    lines = error_lines(
        [_error("util/env_posix.cc", 2), _error("util/env_posix.cc", 3),  # `}` is too short
         _error("util/env_posix.cc", 2, Severity.WARNING), _error("util/gone.cc", 1)],
        root,
    )  # fmt: skip
    assert lines == [POSIX]
    found = echoes(lines, ["util/env_windows.cc"], root)
    assert [(e.file, e.line) for e in found] == [("util/env_windows.cc", 3)]
    text = echo_feedback(found)
    assert "util/env_windows.cc:3" in text
    assert "review_notes" in text


def test_a_green_unit_reports_what_it_couldnt_check() -> None:
    assert unverified_warnings(["a.cc"], [], []) == []
    (changed,) = unverified_warnings(["a.cc", "win.cc"], ["win.cc", "mac.cc"], [])
    assert "unverified: win.cc" in changed
    assert echoes([], ["x"], Path()) == []


def test_the_runner_warns_about_changed_and_still_broken_uncompiled_files(world: World) -> None:
    runner, _ = world.runner([])
    runner.uncompiled = ["src/other.cpp"]
    assert runner.unverified(unit("src/geo.cpp"), [], world.ctx) == []
    (world.worktree.path / "src" / "other.cpp").write_text(
        "int other() { return 1; }\nint broken_line_here = 1;\n", encoding="utf-8"
    )
    runner.error_lines = ["int broken_line_here = 1;"]
    changed, still = runner.unverified(unit("src/geo.cpp"), [], world.ctx)
    assert "src/other.cpp" in changed
    assert "src/other.cpp:2" in still


def test_a_succeeded_unit_names_the_codemods_files_and_keeps_its_baseline(
    world: World, tmp_path: Path, git: GitRunner
) -> None:
    from .test_host import _commits, _host, _start  # pyright: ignore[reportPrivateUsage]

    (world.worktree.path / "src" / "geo.cpp").write_text("typedef int Count;\n", encoding="utf-8")
    world.worktree.commit("a simple typedef")
    snapshot = world.worktree.head()
    host = _host(world)
    cache = BaselineCache(tmp_path / "baselines")
    host.baselines = cache
    state = _start(host, "src/geo.cpp").state
    assert state.commit is not None

    (message,) = _commits(git, world, snapshot)
    assert "Deterministic tools changed src/geo.cpp." in message
    assert parse_trailers(message)["CodeCoreMorph-Models"] == "none"

    stored = cache.get(state.commit, sandbox="native", lang=world.ctx.lang)
    assert stored is not None
    assert stored.tests_from == f"unit {state.run_id}"
    assert stored.tests.passing == ["gtest:Geo.Area"]
    assert stored.tests.failing == ["gtest:Geo.Flaky"]
    assert HostFiles(host.files.root).load().commit == state.commit
