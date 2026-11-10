"""What a C# library depends on outside its own code, in a port's plan (D-82): the types that
only declare native functions have no unit, a unit that calls them links the library, a .NET
package or an operating system's own API refuses the types that use it, and the plan lists each
dependency with what the port does about it, before any unit runs. Deep copies through a
serializer, unsafe code and checked arithmetic are ported, each named in its unit's note."""

from __future__ import annotations

from codecoremorph.core.models import (
    NativeImport,
    NativeLibraryProbe,
    PackageEntry,
    PackageUse,
    PortDependencyKind,
    PortNotice,
    PortNoticeKind,
    UnitStatus,
)
from codecoremorph.planner.csharp import c_runtime, framework_package, library_name, platform_of

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _by_type, _plan

BOX = "csharp:Interop.Libsodium.crypto_box_easy(System.Byte[])"
INIT = "csharp:Interop.Libsodium.sodium_init"
TICKS = "csharp:Sodium.Win32.GetTickCount"


def _sodium(p: FakeProject, probe: NativeLibraryProbe | None) -> Library:
    """A libsodium wrapper: native declarations, the types that call them, and a type for each
    thing a port refuses or takes over."""
    lib = Library(p).project(LIB).project(TESTS, "test")
    p.checked.add(LIB)
    interop = lib.type("Interop", "src/Interop/Interop.Core.cs", kind="class", static=True,
                       visibility="private", native_only=True)  # fmt: skip
    lib.type(
        "Interop.Libsodium", ["src/Interop/Interop.Core.cs", "src/Interop/Interop.Box.cs"],
        static=True, visibility="private", outer=interop, native_only=True,
        native_imports=[
            NativeImport(library="libsodium", entry_point="crypto_box_easy", member=BOX,
                         file="src/Interop/Interop.Box.cs", line=8),
            NativeImport(library="libsodium", entry_point="sodium_init", member=INIT,
                         file="src/Interop/Interop.Core.cs", line=6),
        ],
    )  # fmt: skip
    lib.type("Interop.Libraries", "src/Interop/Interop.Core.cs", static=True,
             visibility="private", outer=interop)  # fmt: skip
    box = lib.type("Sodium.SecretBox", "src/Sodium/SecretBox.cs", members=["Create(System.Byte[])"])
    lib.uses("src/Sodium/SecretBox.cs", f"{box}.Create(System.Byte[])", BOX)
    lib.uses("src/Sodium/SecretBox.cs", f"{box}.Create(System.Byte[])", INIT)
    lib.uses("src/Sodium/SecretBox.cs", f"{box}.Create(System.Byte[])", "csharp:Interop.Libraries")
    json = lib.type(
        "Sodium.Json", "src/Sodium/Json.cs", members=["Save"],
        package_uses=[PackageUse(package="Newtonsoft.Json", file="src/Sodium/Json.cs", line=5,
                                 symbol="Newtonsoft.Json.JsonConvert.SerializeObject"),
                      PackageUse(package="System.Memory", file="src/Sodium/Json.cs", line=6,
                                 symbol="System.Span")],
    )  # fmt: skip
    keys = lib.type("Sodium.KeyStore", "src/Sodium/KeyStore.cs", members=["Save"])
    lib.uses("src/Sodium/KeyStore.cs", f"{keys}.Save", f"{json}.Save")
    win32 = lib.type(
        "Sodium.Win32", "src/Sodium/Win32.cs", static=True, visibility="private",
        native_only=True,
        native_imports=[NativeImport(library="kernel32.dll", entry_point="GetTickCount",
                                     member=TICKS, file="src/Sodium/Win32.cs", line=7)],
    )  # fmt: skip
    ticks = lib.type("Sodium.Ticks", "src/Sodium/Ticks.cs", members=["Now"])
    lib.uses("src/Sodium/Ticks.cs", f"{ticks}.Now", TICKS)
    del win32
    lib.type(
        "Sodium.Book", "src/Sodium/Book.cs", members=["Snapshot"],
        notices=[PortNotice(what=PortNoticeKind.DEEP_COPY, file="src/Sodium/Book.cs", line=52,
                            member="csharp:Sodium.Book.Snapshot",
                            detail="a deep copy of Sodium.Book through a BinaryFormatter round "
                                   "trip in memory")],
    )  # fmt: skip
    lib.type(
        "Sodium.Raw", "src/Sodium/Raw.cs", members=["First(System.Int32[])"],
        notices=[PortNotice(what=PortNoticeKind.UNSAFE, detail="a fixed statement",
                            file="src/Sodium/Raw.cs", line=9)],
    )  # fmt: skip
    p.packages += [
        PackageEntry(project=LIB, name="libsodium", version="1.0.18.2",
                     native=["linux-x64:libsodium.so", "win-x64:libsodium.dll"]),
        PackageEntry(project=LIB, name="Newtonsoft.Json", version="13.0.3",
                     assemblies=["Newtonsoft.Json"]),
        PackageEntry(project=LIB, name="System.Memory", version="4.5.4",
                     assemblies=["System.Memory"]),
    ]  # fmt: skip
    if probe is not None:
        p.native_probes.append(probe)
    return lib


def test_native_libraries_packages_and_platform_apis_in_the_plan(
    fake_project: FakeProject,
) -> None:
    plan = _plan(_sodium(fake_project, NativeLibraryProbe(library="libsodium")).load())
    units = _by_type(plan)
    # The native declarations have no unit: the C++ calls libsodium itself.
    assert not [u for u in plan.units if "Interop" in (u.note or "")]
    assert not [u for u in plan.units if u.port and "csharp:Sodium.Win32" in u.port.types]
    box = units["Sodium.SecretBox"]
    assert box.status is UnitStatus.PENDING
    assert box.needs == []
    assert box.port is not None
    assert box.port.native_libraries == ["libsodium"]
    assert box.port.codemod_options()["native"] == "libsodium"
    note = box.note or ""
    assert "calls libsodium's crypto_box_easy, sodium_init through P/Invoke" in note
    assert "the project checks integer overflow (CheckForOverflowUnderflow)" in note
    # A .NET package refuses what uses it (the framework's own packages don't), and so does
    # an operating system's own API; what uses a refused type is refused too.
    assert units["Sodium.Json"].status is UnitStatus.SKIPPED
    assert (
        "Sodium.Json uses the package Newtonsoft.Json 13.0.3 (Newtonsoft.Json.JsonConvert."
        "SerializeObject (src/Sodium/Json.cs:5)): the new code has no counterpart of a .NET "
        "package" in (units["Sodium.Json"].note or "")
    )
    assert "System.Span" not in (units["Sodium.Json"].note or "")
    assert units["Sodium.KeyStore"].status is UnitStatus.SKIPPED
    assert "uses Sodium.Json, which this port refuses" in (units["Sodium.KeyStore"].note or "")
    assert (
        "Sodium.Ticks calls Windows's own API (kernel32.dll!GetTickCount (src/Sodium/Win32.cs:7)"
        "): the port builds and tests on Linux" in (units["Sodium.Ticks"].note or "")
    )
    # What the port takes over, named in the notes.
    assert (
        "Sodium.Book.Snapshot makes a deep copy of Sodium.Book through a BinaryFormatter round "
        "trip in memory (src/Sodium/Book.cs:52): the new code copies the object graph instead"
        in (units["Sodium.Book"].note or "")
    )
    raw = units["Sodium.Raw"].note or ""
    assert "unsafe code: a fixed statement (src/Sodium/Raw.cs:9)" in raw

    # The plan lists what the library depends on, and what the user must do first.
    deps = {d.name: d for d in plan.dependencies}
    assert set(deps) == {"libsodium", "kernel32.dll", "Newtonsoft.Json"}
    sodium = deps["libsodium"]
    assert (sodium.kind, sodium.version, sodium.ported) == (
        PortDependencyKind.NATIVE_LIBRARY, "1.0.18.2", False,
    )  # fmt: skip
    assert sodium.used_by == ["Sodium.SecretBox"]
    assert "2 functions declared in Interop, 2 of them called by Sodium.SecretBox" in (
        sodium.summary
    )
    assert "the C# loads it from the package libsodium 1.0.18.2 (linux-x64, win-x64)" in (
        sodium.summary
    )
    assert "the toolchain image lacks its header and the library" in sodium.summary
    assert sodium.action is not None
    assert "set sandbox.image" in sodium.action
    assert any(w.startswith("libsodium: the units that call libsodium need") for w in plan.warnings)
    windows = deps["kernel32.dll"]
    assert (windows.kind, windows.ported, windows.used_by) == (
        PortDependencyKind.PLATFORM_API, False, ["Sodium.Ticks"],
    )  # fmt: skip
    package = deps["Newtonsoft.Json"]
    assert (package.kind, package.version, package.ported, package.used_by) == (
        PortDependencyKind.PACKAGE, "13.0.3", False, ["Sodium.Json"],
    )  # fmt: skip


def test_a_native_library_the_image_has(fake_project: FakeProject) -> None:
    probe = NativeLibraryProbe(
        library="libsodium",
        header="/usr/include/sodium.h",
        link="/usr/lib/x86_64-linux-gnu/libsodium.so",
        version="1.0.18",
    )
    plan = _plan(_sodium(fake_project, probe).load())
    (sodium,) = [d for d in plan.dependencies if d.name == "libsodium"]
    assert sodium.ported
    assert sodium.action is None
    assert (
        "the toolchain image has them (/usr/include/sodium.h, "
        "/usr/lib/x86_64-linux-gnu/libsodium.so, version 1.0.18)" in sodium.summary
    )
    assert not [w for w in plan.warnings if w.startswith("libsodium:")]


def test_a_native_run_doesnt_look_for_native_libraries(fake_project: FakeProject) -> None:
    plan = _plan(_sodium(fake_project, None).load())
    (sodium,) = [d for d in plan.dependencies if d.name == "libsodium"]
    assert sodium.ported
    assert "not looked for" in sodium.summary


def test_library_names_and_their_kinds() -> None:
    assert library_name("kernel32.dll") == "kernel32"
    assert library_name("/usr/lib/libSystem.dylib") == "libsystem"
    assert library_name("libsodium.so.23") == "libsodium"
    assert platform_of("KERNEL32.DLL") == "Windows"
    assert platform_of("/usr/lib/libSystem.dylib") == "macOS"
    assert platform_of("libsodium") is None
    assert c_runtime("libc")
    assert c_runtime("libm.so.6")
    assert not c_runtime("libsodium")
    assert framework_package("System.Memory")
    assert framework_package("Microsoft.Bcl.HashCode")
    assert not framework_package("Newtonsoft.Json")
