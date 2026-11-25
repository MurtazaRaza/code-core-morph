"""The runtimes CodeCoreMorph ships for ports from C#, built and checked on their own in the
image for ports from C# (D-97): a .NET value's type and data, an ArgumentOutOfRangeException's
actual value, the token a cancellation carries and a log entry's state, as the bridge's error
record and log call hand them to the façades' support. The C++ runtime builds with ``-Wall
-Wextra -Werror`` under AddressSanitizer and UndefinedBehaviorSanitizer (and its bridge as a
C++17 port builds it); the Rust runtime crate and the bridge's glue pass their tests and
``cargo clippy -D warnings``. What C# does with them is M13's seeded check (D-97). Marked
``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import LanguageConfig
from codecoremorph.sandbox.executor import Executor, full_output

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
ADAPTERS = ROOT / "src" / "codecoremorph" / "adapters"
CPP_RUNTIME = ADAPTERS / "cpp" / "csharp_runtime"
RUST_RUNTIME = ADAPTERS / "rust" / "csharp_runtime"
BRIDGE_HEADER = ADAPTERS / "csharp" / "port_runtime" / "codecoremorph_bridge.h"
DATA = Path(__file__).with_name("data") / "csharp_port_runtimes"

GLUE_MANIFEST = """[package]
name = "glue"
version = "0.1.0"
edition = "2021"
publish = false

[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
codecoremorph_runtime = { path = "../codecoremorph_runtime" }
"""
# As the scaffold's bridge/mod.rs; the glue a port's bridge uses, unused here, is no dead code.
GLUE_MOD = (
    "#![allow(non_snake_case, non_camel_case_types, dead_code)]\n\n"
    "pub mod ccm;\npub mod ccm_logging;\npub mod ccm_tasks;\n"
)


@pytest.fixture
def executor(tmp_path: Path) -> Iterator[tuple[Executor, Path]]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    lang = LanguageConfig(
        language="cpp", standard="cpp20", target_standard="cpp20", port_source="csharp"
    )
    try:
        image = resolve_image(CppAdapter().toolchain_requirements(lang), None)
    except SandboxError as exc:
        pytest.skip(f"no image for ports from C#: {exc}")
    work = tmp_path / "work"
    for path in (work, tmp_path / "build", tmp_path / "cache", tmp_path / "exec"):
        path.mkdir()
    running = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/csharp-port.env",
        )
    )
    running.start()
    try:
        yield running, work
    finally:
        running.stop()


def _sh(executor: Executor, work: Path, script: str, name: str, timeout_s: float = 900) -> str:
    result = executor.run(
        ["sh", "-c", script],
        cwd=executor.to_sandbox_path(work),
        timeout_s=timeout_s,
        log_path=work.parent / "exec" / f"{name}.log",
    )
    output = "\n".join(full_output(result))
    assert result.exit_code == 0, output[-6000:]
    return output


def test_the_cpp_runtime_carries_values_tokens_and_log_state(
    executor: tuple[Executor, Path],
) -> None:
    running, work = executor
    for path in [*CPP_RUNTIME.glob("*.hpp"), *CPP_RUNTIME.glob("*.h"), *CPP_RUNTIME.glob("*.cpp")]:
        shutil.copyfile(path, work / path.name)
    shutil.copyfile(BRIDGE_HEADER, work / BRIDGE_HEADER.name)
    shutil.copyfile(DATA / "check.cpp", work / "check.cpp")
    sources = "codecoremorph_bridge.cpp codecoremorph_logging.cpp codecoremorph_tasks.cpp"
    output = _sh(
        running,
        work,
        "set -e; "
        "clang++ -std=c++20 -Wall -Wextra -Werror -g -fsanitize=address,undefined -pthread -I. "
        f"check.cpp {sources} -o check && ./check; "
        "clang++ -std=c++17 -Wall -Wextra -Werror -I. -c codecoremorph_bridge.cpp -o b17.o; "
        "clang++ -std=c++17 -Wall -Wextra -Werror -I. -c codecoremorph_logging.cpp -o l17.o",
        "cpp-runtime",
    )
    assert "ALL PASSED" in output


def test_the_rust_runtime_carries_values_tokens_and_log_state(
    executor: tuple[Executor, Path],
) -> None:
    running, work = executor
    runtime = work / "codecoremorph_runtime"
    shutil.copytree(RUST_RUNTIME / "codecoremorph_runtime", runtime)
    lib = runtime / "src" / "lib.rs"
    # As the scaffolds place the package runtimes: their modules after the exceptions'.
    lib.write_text(
        lib.read_text(encoding="utf-8").replace(
            "pub mod exceptions;\n", "pub mod exceptions;\npub mod logging;\npub mod tasks;\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    bridge = work / "glue" / "src" / "bridge"
    bridge.mkdir(parents=True)
    for name in ("ccm.rs", "ccm_logging.rs", "ccm_tasks.rs"):
        shutil.copyfile(RUST_RUNTIME / name, bridge / name)
    (bridge / "mod.rs").write_text(GLUE_MOD, encoding="utf-8", newline="\n")
    shutil.copyfile(DATA / "glue_lib.rs", work / "glue" / "src" / "lib.rs")
    (work / "glue" / "Cargo.toml").write_text(GLUE_MANIFEST, encoding="utf-8", newline="\n")
    (work / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["codecoremorph_runtime", "glue"]\nexclude = ["bare", "logging"]\n'
        'resolver = "2"\n',
        encoding="utf-8",
        newline="\n",
    )
    # The crate as a port without async code has it, and without packages: what only the
    # package runtimes use is no dead code there either.
    for name, modules in (("bare", ""), ("logging", "pub mod logging;\n")):
        alone = work / name / "codecoremorph_runtime"
        shutil.copytree(RUST_RUNTIME / "codecoremorph_runtime", alone)
        if not modules:
            for module in ("logging.rs", "tasks.rs"):
                (alone / "src" / module).unlink()
        else:
            (alone / "src" / "tasks.rs").unlink()
            alone_lib = alone / "src" / "lib.rs"
            alone_lib.write_text(
                alone_lib.read_text(encoding="utf-8").replace(
                    "pub mod exceptions;\n", "pub mod exceptions;\n" + modules
                ),
                encoding="utf-8",
                newline="\n",
            )
    target = running.to_sandbox_path(work.parent / "build")
    output = _sh(
        running,
        work,
        f"set -e; export CARGO_TARGET_DIR={target}; "
        "for crate in bare logging; do (cd $crate/codecoremorph_runtime && "
        "cargo clippy --all-targets -- -D warnings 2>&1); done; "
        "cargo test 2>&1; cargo clippy --all-targets -- -D warnings 2>&1",
        "rust-runtime",
    )
    assert "test result: FAILED" not in output
    assert "the_state_names_each_hole_with_its_value ... ok" in output
    assert "tokens_cross ... ok" in output
    assert "actual_values_cross ... ok" in output
