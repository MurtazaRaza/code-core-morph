"""An operating system's own API in a port from C# (D-94): the port is built for Linux, so a call
the index finds only in branches for another platform (a check for Windows holds it) is that
platform's code, which the port doesn't keep, and refuses nothing; a call Linux can reach still
refuses its type; a declaration nothing calls refuses nothing either."""

from __future__ import annotations

from codecoremorph.core.models import NativeCall, NativeImport, UnitStatus

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _by_type, _plan

COUNTER = "csharp:Kit.Native.QueryPerformanceCounter(System.Int64@)"
PID = "csharp:Kit.Native.GetCurrentProcessId"
NAME = "csharp:Kit.Native.GetComputerNameW(System.Text.StringBuilder,System.Int32@)"
UNUSED = "csharp:Kit.Native.Beep(System.UInt32,System.UInt32)"


def _kit(p: FakeProject, *, clock: list[str], host: list[str], native_only: bool = True) -> Library:
    lib = Library(p).project(LIB).project(TESTS, "test")
    native = [
        NativeImport(library="kernel32.dll", entry_point=entry, member=member,
                     file="src/Kit/Native.cs", line=line)
        for entry, member, line in (("QueryPerformanceCounter", COUNTER, 8),
                                    ("GetCurrentProcessId", PID, 11),
                                    ("GetComputerNameW", NAME, 14), ("Beep", UNUSED, 17))
    ]  # fmt: skip
    lib.type("Kit.Native", "src/Kit/Native.cs", static=True, visibility="private",
             native_only=native_only, native_imports=native,
             members=[] if native_only else ["Windows"])  # fmt: skip
    clock_type = "csharp:Kit.Clock"
    lib.type(
        "Kit.Clock", "src/Kit/Clock.cs", static=True, members=["Nanoseconds"],
        native_calls=[NativeCall(member=COUNTER, caller=f"{clock_type}.Nanoseconds",
                                 platforms=clock, file="src/Kit/Clock.cs", line=14)],
    )  # fmt: skip
    lib.uses("src/Kit/Clock.cs", f"{clock_type}.Nanoseconds", COUNTER)
    host_type = "csharp:Kit.Host"
    lib.type(
        "Kit.Host", "src/Kit/Host.cs", static=True, members=["ProcessId", "Name"],
        native_calls=[
            NativeCall(member=PID, caller=f"{host_type}.ProcessId", platforms=host,
                       file="src/Kit/Host.cs", line=9),
            NativeCall(member=NAME, caller=f"{host_type}.Name", platforms=host,
                       file="src/Kit/Host.cs", line=15),
        ],
    )  # fmt: skip
    lib.uses("src/Kit/Host.cs", f"{host_type}.ProcessId", PID)
    lib.uses("src/Kit/Host.cs", f"{host_type}.Name", NAME)
    return lib


def test_a_call_only_in_windows_branches_is_no_refusal(fake_project: FakeProject) -> None:
    plan = _plan(_kit(fake_project, clock=["windows"], host=[]).load())
    units = _by_type(plan)
    clock = units["Kit.Clock"]
    assert clock.status is UnitStatus.PENDING
    assert (
        "calls Windows's own API only where it runs on Windows (QueryPerformanceCounter "
        "(src/Kit/Clock.cs:14)): the port is built for Linux, so it keeps what the C# does "
        "there, and the branches for Windows go (D-94)" in (clock.note or "")
    )
    # Nothing links kernel32 on Linux: its calls are in the branches that go.
    assert clock.port is not None
    assert clock.port.native_libraries == []
    host = units["Kit.Host"]
    assert host.status is UnitStatus.SKIPPED
    assert "Kit.Host calls Windows's own API (kernel32.dll!GetComputerNameW" in (host.note or "")
    assert "the port builds and tests on Linux" in (host.note or "")
    deps = {d.name: d for d in plan.dependencies}
    assert deps["kernel32.dll"].ported is False  # Kit.Host's calls stay C#


def test_every_call_guarded_ports_the_library(fake_project: FakeProject) -> None:
    """Guarded for Windows, or for anything but Linux ("!linux"); the declaration nothing calls
    (Beep) goes with the retirement."""
    plan = _plan(_kit(fake_project, clock=["windows"], host=["!linux"]).load())
    units = _by_type(plan)
    assert units["Kit.Clock"].status is UnitStatus.PENDING
    assert units["Kit.Host"].status is UnitStatus.PENDING
    kernel32 = {d.name: d for d in plan.dependencies}["kernel32.dll"]
    assert kernel32.ported is True
    assert "only where it runs on Windows (a check for windows holds each call)" in kernel32.summary
    assert "the declarations go with the retirement (D-94)" in kernel32.summary


def test_runs_on() -> None:
    def call(*platforms: str) -> NativeCall:
        return NativeCall(member=PID, platforms=list(platforms), file="a.cs", line=1)

    assert call().runs_on("linux")
    assert not call("windows").runs_on("linux")
    assert call("windows").runs_on("windows")
    assert call("!windows").runs_on("linux")
    assert not call("!windows").runs_on("windows")
    assert not call("!linux", "!osx").runs_on("linux")
    assert call("linux", "!windows").runs_on("linux")


def test_the_declaring_type_answers_for_no_other_units_calls(fake_project: FakeProject) -> None:
    """A type that declares the P/Invokes beside members of its own (a setting; a flag set from
    a platform check would leave it native-only) has a unit; Kit.Host's reachable call refuses
    Kit.Host, not the declaring type."""
    plan = _plan(_kit(fake_project, clock=["windows"], host=[], native_only=False).load())
    units = _by_type(plan)
    assert units["Kit.Native"].status is UnitStatus.PENDING
    assert units["Kit.Clock"].status is UnitStatus.PENDING
    assert units["Kit.Host"].status is UnitStatus.SKIPPED
