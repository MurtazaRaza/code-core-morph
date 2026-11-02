"""NativeExecutor against real processes (SPEC §7.8)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from codecoremorph.core.errors import SandboxError
from codecoremorph.core.processes import pid_is_running
from codecoremorph.sandbox import NativeExecutor

PY = sys.executable


@pytest.fixture
def executor(tmp_path: Path) -> NativeExecutor:
    ex = NativeExecutor(log_dir=tmp_path / "logs", output_cap=1024)
    ex.start()
    return ex


def test_exit_code_output_and_log(executor: NativeExecutor, tmp_path: Path) -> None:
    result = executor.run(
        [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        cwd=str(tmp_path),
        timeout_s=60,
    )
    assert (result.exit_code, result.timed_out, result.ok) == (3, False, False)
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
    log = result.log_path.read_text(encoding="utf-8")
    assert "exit code: 3" in log
    assert "out" in log
    assert "err" in log


def test_reduced_isolation_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    NativeExecutor(log_dir=tmp_path).start()
    assert "run directly on this machine" in caplog.text


def test_credentials_in_the_host_environment_never_reach_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "z" * 30)
    monkeypatch.setenv("MY_SERVICE_TOKEN", "opaque-token-value")
    ex = NativeExecutor(log_dir=tmp_path / "logs")
    result = ex.run(
        [PY, "-c", "import os; print(sorted(os.environ))"], cwd=str(tmp_path), timeout_s=60
    )
    assert "ANTHROPIC_API_KEY" not in result.stdout
    assert "MY_SERVICE_TOKEN" not in result.stdout
    assert "PATH" in result.stdout.upper()
    with pytest.raises(SandboxError):
        ex.run([PY, "-V"], cwd=str(tmp_path), timeout_s=60, env={"GH_TOKEN": "x"})


def test_toolchain_env_file_is_applied(tmp_path: Path) -> None:
    tools = tmp_path / "toolchain" / "bin"
    tools.mkdir(parents=True)
    env_file = tmp_path / "cpp.env"
    env_file.write_text(f"PATH_PREPEND={tools}\nCXX=clang++-pinned\n", encoding="utf-8")
    ex = NativeExecutor(log_dir=tmp_path / "logs", toolchain_env=env_file)
    result = ex.run(
        [PY, "-c", "import os; print(os.environ['CXX']); print(os.environ['PATH'])"],
        cwd=str(tmp_path),
        timeout_s=60,
        env={"EXTRA": "1"},
    )
    lines = result.stdout.splitlines()
    assert lines[0] == "clang++-pinned"
    assert lines[1].startswith(str(tools))


def test_programs_are_resolved_with_the_commands_own_path(tmp_path: Path) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    if sys.platform == "win32":
        program = tools / "ccm-hello.bat"
        program.write_bytes(b"@echo hello from toolchain\r\n")
    else:
        program = tools / "ccm-hello"
        program.write_bytes(b"#!/bin/sh\necho hello from toolchain\n")
        program.chmod(0o755)
    env_file = tmp_path / "x.env"
    env_file.write_text(f"PATH_PREPEND={tools}\n", encoding="utf-8")
    ex = NativeExecutor(log_dir=tmp_path / "logs", toolchain_env=env_file)
    result = ex.run(["ccm-hello"], cwd=str(tmp_path), timeout_s=60)
    assert result.exit_code == 0, result.stderr
    assert "hello from toolchain" in result.stdout


def test_missing_program_is_exit_127(executor: NativeExecutor, tmp_path: Path) -> None:
    result = executor.run(["ccm-no-such-program"], cwd=str(tmp_path), timeout_s=60)
    assert result.exit_code == 127
    assert "command not found" in result.stderr


def test_missing_working_directory(executor: NativeExecutor, tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="doesn't exist"):
        executor.run([PY, "-V"], cwd=str(tmp_path / "nope"), timeout_s=60)


def test_stdin(executor: NativeExecutor, tmp_path: Path) -> None:
    result = executor.run(
        [PY, "-c", "import sys; print(sys.stdin.read()[::-1])"],
        cwd=str(tmp_path),
        timeout_s=60,
        stdin=b"abc",
    )
    assert result.stdout.strip() == "cba"


def test_output_is_capped_but_logged_in_full(executor: NativeExecutor, tmp_path: Path) -> None:
    result = executor.run(
        [PY, "-c", "print('x' * 5000); print('END')"],
        cwd=str(tmp_path),
        timeout_s=60,
        log_path=tmp_path / "custom" / "build.log",
    )
    assert result.stdout_truncated
    assert len(result.stdout) < 1200
    assert result.stdout.rstrip().endswith("END")
    assert result.log_path == tmp_path / "custom" / "build.log"
    assert "x" * 5000 in result.log_path.read_text(encoding="utf-8")


def test_timeout_kills_the_whole_process_tree(executor: NativeExecutor, tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    child = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{pid_file}', 'w').write(str(p.pid))\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    result = executor.run([PY, "-c", child], cwd=str(tmp_path), timeout_s=3)
    elapsed = time.monotonic() - started
    assert result.timed_out
    assert not result.ok
    assert elapsed < 30
    grandchild = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while pid_is_running(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_is_running(grandchild)
    assert "timed out" in result.log_path.read_text(encoding="utf-8")


def test_network_and_paths_are_passthrough(executor: NativeExecutor, tmp_path: Path) -> None:
    executor.set_network(False)  # native mode can't restrict the network; never fails
    assert executor.to_sandbox_path(tmp_path / "x") == str(tmp_path / "x")
    executor.stop()
