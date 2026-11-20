"""An operating system's own library in a C# port's source, with the real toolchain (D-94): on
``fixtures/csharp/hostkit``, the Roslyn helper records each P/Invoke call with the platforms its
site runs on (an ``if`` on a field set from ``RuntimeInformation.IsOSPlatform``, a ``?:``, an
early return), and the plan, built for Linux, ports every type, the Windows branches named in
the notes and kernel32 carried over, but Native, which only declares the functions beside its
platform check.

Native runs are marked ``toolchain``; Docker runs ``docker``. The restore needs the network.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import UnitStatus
from codecoremorph.sandbox.executor import Executor
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.store.index import IndexStore

from .test_csharp_port_index import LANG, _cut  # pyright: ignore[reportPrivateUsage]

ROOT = Path(__file__).resolve().parents[3]
HOSTKIT = ROOT / "fixtures" / "csharp" / "hostkit"


@pytest.fixture(params=[pytest.param("native", marks=pytest.mark.toolchain),
                        pytest.param("docker", marks=pytest.mark.docker)])  # fmt: skip
def ctx(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[AdapterContext]:
    work = tmp_path / "hostkit"
    shutil.copytree(HOSTKIT, work)
    short = Path(tempfile.mkdtemp(prefix="ccm"))  # NuGet's paths and Windows' 260 characters
    for path in (tmp_path / "build", short / "cache", tmp_path / "logs"):
        path.mkdir()
    executor: Executor
    if request.param == "native":
        env: Path = request.getfixturevalue("csharp_toolchain_env")
        executor = NativeExecutor(log_dir=tmp_path / "exec", toolchain_env=env)
    else:
        from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
        from codecoremorph.sandbox.provisioning import resolve_image

        try:
            image = resolve_image(CSharpAdapter().toolchain_requirements(LANG), None)
        except SandboxError as exc:
            pytest.skip(f"no C# toolchain image: {exc}")
        executor = DockerExecutor(
            DockerSettings(
                image=image,
                run_id=new_run_id(),
                worktree=work,
                build_dir=tmp_path / "build",
                cache_dir=short / "cache",
                log_dir=tmp_path / "exec",
                toolchain_env="/opt/codecoremorph/toolchains/csharp.env",
            )
        )
    executor.start()
    try:
        yield AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=short / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=LANG,
        )
    finally:
        executor.stop()
        shutil.rmtree(
            f"\\\\?\\{short.resolve()}" if sys.platform == "win32" else short, ignore_errors=True
        )


def test_each_call_and_the_platforms_it_runs_on(ctx: AdapterContext) -> None:
    adapter = CSharpAdapter()
    assert adapter.configure(ctx).success
    index = adapter.index(ctx)
    assert not index.problems
    with IndexStore.open(index.path) as store:
        calls = {
            (t.name, c.member.split("(")[0].rsplit(".", 1)[-1]): c.platforms
            for t in store.port_types()
            for c in t.native_calls
        }
        native_only = {t.name: t.native_only for t in store.port_types()}
    assert calls == {
        # if (Native.Windows) { ... }, Native.Windows set from IsOSPlatform(OSPlatform.Windows)
        ("Hostkit.Clock", "QueryPerformanceCounter"): ["windows"],
        ("Hostkit.Clock", "QueryPerformanceFrequency"): ["windows"],
        # after `if (Native.Windows) { ... return ...; }`: everywhere else
        ("Hostkit.Clock", "ClockGetTime"): ["!windows"],
        # Native.Windows ? kernel32 : libc
        ("Hostkit.Host", "GetCurrentProcessId"): ["windows"],
        ("Hostkit.Host", "GetPid"): ["!windows"],
        ("Hostkit.Host", "FormatMessageW"): ["windows"],
        ("Hostkit.Host", "StrError"): ["!windows"],
        # if (!Native.Windows) return true; then the console's mode
        ("Hostkit.Terminal", "GetStdHandle"): ["windows"],
        ("Hostkit.Terminal", "GetConsoleMode"): ["windows"],
        ("Hostkit.Terminal", "SetConsoleMode"): ["windows"],
    }
    # Native declares the functions, with a field set from a platform check: no unit (D-82, D-94).
    assert native_only["Hostkit.Native"]
    plan = _cut(adapter, ctx, index.path)
    assert all(u.status is UnitStatus.PENDING for u in plan.units), [
        (u.id, u.note) for u in plan.units if u.status is not UnitStatus.PENDING
    ]
    assert [u.port.types for u in plan.units if u.port and u.port.types] == [
        ["csharp:Hostkit.Clock"],
        ["csharp:Hostkit.Host"],
        ["csharp:Hostkit.Terminal"],
    ]
    clock = next(u for u in plan.units if u.port and "csharp:Hostkit.Clock" in u.port.types)
    assert "only where it runs on Windows (QueryPerformanceCounter" in (clock.note or "")
    kernel32 = {d.name: d for d in plan.dependencies}["kernel32.dll"]
    assert kernel32.ported
