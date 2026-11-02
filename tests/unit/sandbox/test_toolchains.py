"""Native toolchain helpers: install commands, installed-tool listing, tool checks (FR-1.3)."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.models import ExecResult, ToolchainSpec, ToolRequirement
from codecoremorph.core.paths import HomeLayout
from codecoremorph.sandbox import toolchains
from codecoremorph.sandbox.executor import COMMAND_NOT_FOUND


def test_install_command_uses_the_platform_script() -> None:
    command = toolchains.install_command("cpp", force=True, dry_run=True, prefix=Path("x"))
    if sys.platform == "win32":
        assert command[:3] == ["cmd.exe", "/d", "/c"]
        assert command[3].endswith("install-toolchain.bat")
    else:
        assert command[0] == "sh"
        assert command[1].endswith("install-toolchain.sh")
    assert command[-5:] == ["cpp", "--force", "--dry-run", "--prefix", "x"]
    with pytest.raises(SandboxError, match="available: cpp"):
        toolchains.install_command("cobol")


def test_installed_tools_compare_markers_with_the_manifest(tmp_path: Path) -> None:
    home = HomeLayout(tmp_path)
    tools = toolchains.installed_tools(home, "cpp")
    names = {t.tool for t in tools}
    assert {"cmake", "ninja", "googletest"} <= names
    assert not any(t.installed for t in tools)

    cmake = next(t for t in tools if t.tool == "cmake")
    manifest = (toolchains.scripts_dir() / "deps" / "manifest.txt").read_text(encoding="utf-8")
    plat = toolchains.current_platform()
    sha = next(
        line.split("|")[5]
        for line in manifest.splitlines()
        if line.startswith(f"cpp|cmake|{cmake.version}|{plat}|")
    )
    cmake.directory.mkdir(parents=True)
    (cmake.directory / ".installed").write_text(f"{sha}\r\nmembers:\r\n", encoding="utf-8")
    again = {t.tool: t.installed for t in toolchains.installed_tools(home, "cpp")}
    assert again["cmake"]
    assert not again["ninja"]


class FakeExecutor:
    """Answers version commands from a table."""

    def __init__(self, outputs: Mapping[str, tuple[int, str]]):
        self.outputs = outputs

    @property
    def kind(self) -> str:
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
        code, text = self.outputs.get(argv[0], (COMMAND_NOT_FOUND, ""))
        return ExecResult(
            argv=list(argv), exit_code=code, stdout=text, stderr="", duration_s=0,
            log_path=Path("x.log"),
        )  # fmt: skip


SPEC = ToolchainSpec(
    language="cpp",
    image="codecoremorph-cpp",
    tools=[
        ToolRequirement(name="clang", min_version="18"),
        ToolRequirement(name="cmake", min_version="3.21"),
        ToolRequirement(name="ninja", min_version="1.10"),
        ToolRequirement(name="ctest"),
        ToolRequirement(name="odd"),
    ],
    install_hint="Run codecoremorph toolchain install cpp.",
)


def test_check_tools_reports_each_problem() -> None:
    executor = FakeExecutor(
        {
            "clang": (0, "clang version 18.1.8 (https://github.com/llvm/llvm-project)"),
            "cmake": (0, "cmake version 3.16.3\n\nCMake suite maintained by Kitware"),
            "ctest": (0, "ctest version 4.4.5"),
            "odd": (0, "no version here"),
        }
    )
    results = {r.name: r for r in toolchains.check_tools(executor, SPEC, cwd=".")}  # type: ignore[arg-type]
    assert results["clang"].ok
    assert results["clang"].version == "18.1.8"
    assert not results["cmake"].ok
    assert "older than the required 3.21" in results["cmake"].detail
    assert results["ninja"].detail == "not found"
    assert results["ctest"].ok
    assert not results["odd"].ok

    with pytest.raises(SandboxError) as error:
        toolchains.require_tools(executor, SPEC, cwd=".")  # type: ignore[arg-type]
    assert "cmake: version 3.16.3" in str(error.value.reason)
    assert "ninja: not found" in str(error.value.reason)
    assert error.value.hint == SPEC.install_hint
