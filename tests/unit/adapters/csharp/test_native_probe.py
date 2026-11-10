"""What the toolchain image has of each native library a port's source calls (D-82): looked for
in Docker, where the toolchain is the image's, and not in a native run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, cast

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp.indexing import probe_native
from codecoremorph.core.models import ExecResult, LanguageConfig, NativeLibraryProbe
from codecoremorph.sandbox.executor import Executor


class _Probe:
    """An executor that answers the probe with canned lines."""

    def __init__(self, kind: Literal["docker", "native"], stdout: str, log: Path) -> None:
        self._kind: Literal["docker", "native"] = kind
        self.stdout = stdout
        self.log = log
        self.argv: list[str] = []

    @property
    def kind(self) -> Literal["docker", "native"]:
        return self._kind

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
        self.argv = list(argv)
        return ExecResult(argv=list(argv), exit_code=0, stdout=self.stdout, stderr="",
                          duration_s=0.1, log_path=self.log)  # fmt: skip

    def to_sandbox_path(self, host_path: Path) -> str:
        return "/workspace"


def _ctx(tmp_path: Path, executor: _Probe) -> AdapterContext:
    return AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=cast(Executor, executor),
        lang=LanguageConfig(language="csharp", standard="csharp9"),
    )


def test_each_library_with_its_header_library_and_version(tmp_path: Path) -> None:
    stdout = (
        "libsodium\t/usr/include/sodium.h\t/usr/lib/x86_64-linux-gnu/libsodium.so\t1.0.18\n"
        "libzstd\t\t/usr/lib/x86_64-linux-gnu/libzstd.so\t\n"
        "noise line\n"
    )
    executor = _Probe("docker", stdout, tmp_path / "log")
    found = probe_native(_ctx(tmp_path, executor), ["libsodium", "libzstd"])
    assert executor.argv[:2] == ["sh", "-c"]
    assert executor.argv[-2:] == ["libsodium", "libzstd"]
    assert found == [
        NativeLibraryProbe(
            library="libsodium",
            header="/usr/include/sodium.h",
            link="/usr/lib/x86_64-linux-gnu/libsodium.so",
            version="1.0.18",
        ),
        NativeLibraryProbe(library="libzstd", link="/usr/lib/x86_64-linux-gnu/libzstd.so"),
    ]


def test_a_native_run_looks_for_nothing(tmp_path: Path) -> None:
    executor = _Probe("native", "libsodium\t/x.h\t/x.so\t1\n", tmp_path / "log")
    assert probe_native(_ctx(tmp_path, executor), ["libsodium"]) == []
    assert executor.argv == []
    assert probe_native(_ctx(tmp_path, _Probe("docker", "", tmp_path / "log")), []) == []
