"""The C++ adapter across standards: its standards list, C++98/03 configure, and the parse
standard of clang-tidy passes (D-38). No toolchain needed: commands are recorded."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.adapters.cpp.cmake import SUPPORTED_STANDARDS
from codecoremorph.core.models import CodemodSpec, LanguageConfig
from codecoremorph.sandbox.executor import ExecResult


class RecordingExecutor:
    """Succeeds at everything and remembers each command line."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    @property
    def kind(self) -> Literal["native"]:
        return "native"

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        timeout_s: float,
        stdin: bytes | None = None,
        log_path: Path | None = None,
    ) -> ExecResult:
        self.commands.append(list(argv))
        return ExecResult(
            argv=list(argv), exit_code=0, stdout="", stderr="", duration_s=0,
            log_path=log_path or Path("x.log"),
        )  # fmt: skip

    def to_sandbox_path(self, host_path: Path) -> str:
        return host_path.as_posix()

    def tidy_commands(self) -> list[list[str]]:
        return [c for c in self.commands if c[0] == "clang-tidy"]


def _context(tmp_path: Path, standard: str, target: str | None = None) -> AdapterContext:
    for name in ("work", "build", "cache", "logs"):
        (tmp_path / name).mkdir(exist_ok=True)
    return AdapterContext(
        worktree=tmp_path / "work",
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=RecordingExecutor(),  # type: ignore[arg-type]
        lang=LanguageConfig(language="cpp", standard=standard, target_standard=target),
    )


def _compile_commands(ctx: AdapterContext, std_flag: str) -> None:
    work = ctx.worktree.as_posix()
    entries = [
        {"directory": ctx.build_dir.as_posix(), "file": f"{work}/src/a.cpp",
         "command": f"clang++ {std_flag} -c {work}/src/a.cpp"},
        {"directory": ctx.build_dir.as_posix(), "file": f"{work}/src/b.c",
         "command": f"clang -std=gnu11 -c {work}/src/b.c"},
    ]  # fmt: skip
    (ctx.cache_dir / "compile_commands.json").write_text(json.dumps(entries), encoding="utf-8")


def _recorder(ctx: AdapterContext) -> RecordingExecutor:
    executor = ctx.executor
    assert isinstance(executor, RecordingExecutor)
    return executor


def test_capabilities_list_the_standards_oldest_first() -> None:
    standards = CppAdapter().capabilities().standards
    assert standards == list(SUPPORTED_STANDARDS)
    assert standards == ["cpp98", "cpp03", "cpp11", "cpp14", "cpp17", "cpp20", "cpp23"]


@pytest.mark.parametrize("standard", ["cpp98", "cpp03"])
def test_pre_cpp11_configure_uses_the_cpp98_googletest(tmp_path: Path, standard: str) -> None:
    ctx = _context(tmp_path, standard)
    CppAdapter().configure(ctx)
    configure = _recorder(ctx).commands[0]
    script = ctx.cache_dir / "cxx98-initial-cache.cmake"
    assert configure[1:3] == ["-C", script.as_posix()]
    assert "-DCMAKE_CXX_STANDARD=98" in configure
    assert "CODECOREMORPH_CXX98_PREFIX_PATH" in script.read_text(encoding="utf-8")


def test_cpp11_configure_has_no_initial_cache(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "cpp11")
    CppAdapter().configure(ctx)
    assert "-C" not in _recorder(ctx).commands[0]


@pytest.mark.parametrize(
    ("std_flag", "pass_standard", "expected"),
    [
        ("-std=gnu++98", "cpp11", "--extra-arg=-std=gnu++11"),  # raised; GNU dialect kept
        ("-std=c++98", "cpp17", "--extra-arg=-std=c++17"),
        ("-std=gnu++14", "cpp11", None),  # already at least C++11
        ("-std=gnu++1z", "cpp17", None),  # old spelling of C++17
    ],
)
def test_clang_tidy_pass_parses_under_its_standard(
    tmp_path: Path, std_flag: str, pass_standard: str, expected: str | None
) -> None:
    ctx = _context(tmp_path, "cpp98", "cpp20")
    _compile_commands(ctx, std_flag)
    spec = CodemodSpec(tool="clang-tidy", checks=["modernize-x"], standard=pass_standard)
    CppAdapter().run_codemods(ctx, [spec], ["src/a.cpp", "src/b.c"])
    [tidy] = _recorder(ctx).tidy_commands()
    extra = [a for a in tidy if a.startswith("--extra-arg=")]
    assert extra == ([expected] if expected else [])
    # A C++ -std= flag would break the C unit, so a raised pass leaves it out.
    assert any(a.endswith("/src/b.c") for a in tidy) == (expected is None)


def test_clang_tidy_pass_newer_than_the_target_is_skipped(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "cpp11", "cpp14")
    _compile_commands(ctx, "-std=gnu++11")
    spec = CodemodSpec(tool="clang-tidy", checks=["modernize-x"], standard="cpp17")
    result = CppAdapter().run_codemods(ctx, [spec], ["src/a.cpp"])
    assert _recorder(ctx).tidy_commands() == []
    assert result.changes == []


def test_clang_tidy_pass_without_a_standard_runs_as_built(tmp_path: Path) -> None:
    ctx = _context(tmp_path, "cpp98", "cpp20")
    _compile_commands(ctx, "-std=gnu++98")
    CppAdapter().run_codemods(
        ctx, [CodemodSpec(tool="clang-tidy", checks=["modernize-x"])], ["src/a.cpp"]
    )
    [tidy] = _recorder(ctx).tidy_commands()
    assert not any(a.startswith("--extra-arg=") for a in tidy)
