from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from codecoremorph.core.errors import SandboxError
from codecoremorph.sandbox.executor import LogNamer, check_command_env, finish_result
from codecoremorph.sandbox.native.env import (
    apply_toolchain_env,
    merge_environment,
    parse_env_file,
    scrubbed_environment,
)

HOST = {
    "PATH": "/usr/bin",
    "HOME": "/home/u",
    "SYSTEMROOT": "C:\\Windows",
    "ANTHROPIC_API_KEY": "sk-ant-secret-value",
    "GITHUB_TOKEN": "ghp_x",
    "AWS_SECRET_ACCESS_KEY": "s3cr3t",
    "SOME_RANDOM_VAR": "x",
    "LANG": "en_US.UTF-8",
}


def test_scrubbed_environment_keeps_only_allowlisted_variables() -> None:
    env = scrubbed_environment(HOST)
    assert "PATH" in env
    assert "LANG" in env
    for secret in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SOME_RANDOM_VAR"):
        assert secret not in env
    assert ("SYSTEMROOT" in env) is (sys.platform == "win32")
    assert ("HOME" in env) is (sys.platform != "win32")


def test_allowlist_extensions_never_let_secrets_through() -> None:
    env = scrubbed_environment(HOST, extra_allowlist=["SOME_RANDOM_VAR", "ANTHROPIC_API_KEY"])
    assert env["SOME_RANDOM_VAR"] == "x"
    assert "ANTHROPIC_API_KEY" not in env


def test_parse_env_file() -> None:
    text = '# toolchain\n\nCC=/opt/llvm/bin/clang\nCXX = "/opt/llvm/bin/clang++"\nEMPTY=\n'
    assert parse_env_file(text) == {
        "CC": "/opt/llvm/bin/clang",
        "CXX": "/opt/llvm/bin/clang++",
        "EMPTY": "",
    }
    with pytest.raises(ValueError, match="line 1"):
        parse_env_file("not a pair")


def test_path_prepend() -> None:
    env = apply_toolchain_env({"PATH": "base"}, {"PATH_PREPEND": "tools", "CC": "clang"})
    assert env["PATH"] == f"tools{os.pathsep}base"
    assert env["CC"] == "clang"
    assert apply_toolchain_env({}, {"PATH_PREPEND": "tools"}) == {"PATH": "tools"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows variable names ignore case")
def test_merge_is_case_insensitive_on_windows() -> None:
    merged = merge_environment({"Path": "a", "TEMP": "t"}, {"PATH": "b"})
    assert merged == {"TEMP": "t", "PATH": "b"}


def test_command_env_refuses_credentials() -> None:
    check_command_env({"CC": "clang", "CMAKE_BUILD_TYPE": "Debug"})
    check_command_env(None)
    with pytest.raises(SandboxError, match="OPENAI_API_KEY"):
        check_command_env({"OPENAI_API_KEY": "x"})
    with pytest.raises(SandboxError, match="FLAGS"):
        check_command_env({"FLAGS": "--token sk-ant-api03-" + "a" * 30})


def test_log_names(tmp_path: Path) -> None:
    namer = LogNamer(tmp_path)
    assert namer.next("/usr/bin/cmake").name == "0001-cmake.log"
    assert namer.next("C:\\tools\\ninja.exe").name == "0002-ninja.exe.log"
    assert namer.next("weird name;rm -rf").name == "0003-weird_name_rm_-rf.log"


def test_finish_result_caps_output_and_keeps_the_full_log(tmp_path: Path) -> None:
    out, err = tmp_path / "out", tmp_path / "err"
    out.write_bytes(b"A" * 100 + b"B" * 100)
    err.write_bytes("short ✓".encode())
    log = tmp_path / "logs" / "0001-tool.log"
    result = finish_result(
        argv=["tool", "--flag"],
        cwd="/workspace",
        stdout_file=out,
        stderr_file=err,
        log_path=log,
        exit_code=2,
        duration_s=1.25,
        timed_out=False,
        output_cap=40,
    )
    assert result.stdout_truncated
    assert not result.stderr_truncated
    assert result.stdout.startswith("A" * 20)
    assert result.stdout.endswith("B" * 20)
    assert "160 bytes omitted" in result.stdout
    assert result.stderr == "short ✓"
    assert not result.ok
    text = log.read_bytes()
    assert text.startswith(b"$ tool --flag\ncwd: /workspace\n")
    assert b"A" * 100 + b"B" * 100 in text
    assert not out.exists()
    assert not err.exists()
