"""A .NET package with a counterpart in a port's target (D-95): a type that uses only what the
counterpart covers is ported, its unit naming the packages for the scaffold and linking the C
libraries that stand for them; a use outside the counterpart's API refuses the type, by site;
and the plan's dependency table says what stands for each package, how it reaches the build and
what has to happen first. A package CodeCoreMorph doesn't map still refuses what uses it."""

from __future__ import annotations

from codecoremorph.adapters.csharp import packages as mappings
from codecoremorph.core.models import (
    NativeLibraryProbe,
    PackageEntry,
    PackageUse,
    PortDependencyKind,
    UnitStatus,
)
from codecoremorph.planner.csharp import mapped_packages_in

from .conftest import FakeProject
from .test_csharp_units import LIB, TESTS, Library, _by_type, _plan

LZ4 = "K4os.Compression.LZ4"
XXHASH = "K4os.Hash.xxHash"
LOGGING = "Microsoft.Extensions.Logging.Abstractions"


def _use(package: str, symbol: str, file: str, line: int) -> PackageUse:
    return PackageUse(package=package, symbol=symbol, file=file, line=line)


def _stash(p: FakeProject, *, versions: dict[str, str] | None = None) -> Library:
    """A library over three packages: a codec over LZ4 and xxHash, a store that logs, a type
    that opens a logging scope (which the counterpart doesn't cover), and a test double."""
    lib = Library(p).project(LIB).project(TESTS, "test", references=[LIB])
    codec = lib.type(
        "Zoo.Codec", "src/Zoo/Codec.cs", static=True, members=["Pack(System.Byte[])"],
        package_uses=[
            _use(LZ4, "K4os.Compression.LZ4.LZ4Codec.Encode", "src/Zoo/Codec.cs", 9),
            _use(LZ4, "K4os.Compression.LZ4.LZ4Level", "src/Zoo/Codec.cs", 10),
            _use(XXHASH, "K4os.Hash.xxHash.XXH64.DigestOf", "src/Zoo/Codec.cs", 12),
        ],
    )  # fmt: skip
    store = lib.type(
        "Zoo.Store", "src/Zoo/Store.cs", members=["Put(System.Byte[])"],
        package_uses=[
            _use(LOGGING, "Microsoft.Extensions.Logging.ILogger<Zoo.Store>", "src/Zoo/Store.cs", 8),
            _use(LOGGING, "Microsoft.Extensions.Logging.LoggerExtensions.LogDebug",
                 "src/Zoo/Store.cs", 15),
        ],
    )  # fmt: skip
    lib.uses("src/Zoo/Store.cs", f"{store}.Put(System.Byte[])", f"{codec}.Pack(System.Byte[])")
    lib.type(
        "Zoo.Scoped", "src/Zoo/Scoped.cs", members=["Run"],
        package_uses=[
            _use(LOGGING, "Microsoft.Extensions.Logging.ILogger.BeginScope", "src/Zoo/Scoped.cs", 7)
        ],
    )  # fmt: skip
    lib.type(
        "Zoo.Tests.ListLogger", "tests/Zoo.Tests/ListLogger.cs", project=TESTS,
        package_uses=[
            _use(LOGGING, "Microsoft.Extensions.Logging.ILogger",
                 "tests/Zoo.Tests/ListLogger.cs", 4)
        ],
    )  # fmt: skip
    wanted = {LZ4: "1.3.8", XXHASH: "1.0.8", LOGGING: "8.0.2", **(versions or {})}
    for name, version in wanted.items():
        for project in (LIB, TESTS):
            p.packages.append(
                PackageEntry(
                    project=project,
                    name=name,
                    version=version,
                    counterpart=mappings.counterpart(name, version, "cpp"),
                )
            )
    return lib


def test_types_that_use_mapped_packages_are_ported(fake_project: FakeProject) -> None:
    plan = _plan(_stash(fake_project).load())
    units = _by_type(plan)
    codec = units["Zoo.Codec"]
    assert codec.status is UnitStatus.PENDING
    assert codec.port is not None
    assert codec.port.packages == [LZ4, XXHASH]
    assert codec.port.native_libraries == ["lz4", "xxhash"]
    assert codec.port.codemod_options()["packages"] == f"{LZ4},{XXHASH}"
    note = codec.note or ""
    assert (
        f"uses the package {LZ4} 1.3.8 (K4os.Compression.LZ4.LZ4Codec.Encode, "
        "K4os.Compression.LZ4.LZ4Level): the C++ uses liblz4 instead, which the scaffold links "
        "(lz4) (D-95)"
    ) in note
    assert "the C++ uses libxxhash instead" in note
    store = units["Zoo.Store"]
    assert store.status is UnitStatus.PENDING
    assert store.port is not None
    assert store.port.packages == [LOGGING]
    assert store.port.native_libraries == []
    assert "which the scaffold places with the port (D-95)" in (store.note or "")


def test_a_use_the_counterpart_doesnt_cover_refuses_the_type(fake_project: FakeProject) -> None:
    plan = _plan(_stash(fake_project).load())
    scoped = _by_type(plan)["Zoo.Scoped"]
    assert scoped.status is UnitStatus.SKIPPED
    assert (
        "Zoo.Scoped uses what ccm::logging (codecoremorph_logging.hpp, which CodeCoreMorph "
        f"ships), the C++ counterpart of {LOGGING} 8.0.2, doesn't cover: "
        "Microsoft.Extensions.Logging.ILogger.BeginScope (src/Zoo/Scoped.cs:7) (D-95)"
    ) in (scoped.note or "")


def test_a_version_the_mapping_wasnt_checked_on_refuses_the_type(
    fake_project: FakeProject,
) -> None:
    plan = _plan(_stash(fake_project, versions={LZ4: "2.0.1"}).load())
    codec = _by_type(plan)["Zoo.Codec"]
    assert codec.status is UnitStatus.SKIPPED
    assert (
        f"Zoo.Codec uses the package {LZ4} 2.0.1 (K4os.Compression.LZ4.LZ4Codec.Encode "
        "(src/Zoo/Codec.cs:9), K4os.Compression.LZ4.LZ4Level (src/Zoo/Codec.cs:10)): the new "
        "code has no counterpart of a .NET package"
    ) in (codec.note or "")
    (lz4,) = [d for d in plan.dependencies if d.name == LZ4]
    assert not lz4.ported


def test_the_dependency_table_says_what_stands_for_each_package(
    fake_project: FakeProject,
) -> None:
    lib = _stash(fake_project)
    fake_project.native_probes += [
        NativeLibraryProbe(library="lz4", header="/usr/include/lz4.h",
                           link="/usr/lib/x86_64-linux-gnu/liblz4.so", version="1.9.3"),
        NativeLibraryProbe(library="xxhash"),
    ]  # fmt: skip
    plan = _plan(lib.load())
    deps = {d.name: d for d in plan.dependencies}
    lz4, xxhash, logging = deps[LZ4], deps[XXHASH], deps[LOGGING]
    assert {d.kind for d in (lz4, xxhash, logging)} == {PortDependencyKind.PACKAGE}
    assert lz4.ported
    assert lz4.action is None
    assert "the C++ uses liblz4 instead (D-95), checked 1.3.8 against liblz4 1.9.3" in lz4.summary
    assert (
        "The new code includes lz4.h, lz4hc.h and links lz4, so the build needs their "
        "development files; the toolchain image has lz4 (/usr/include/lz4.h"
    ) in lz4.summary
    assert "a second implementation the counterpart gives the same results as" in lz4.summary
    # The image lacks xxhash's files: the units that use it wait for an image with them.
    assert not xxhash.ported
    assert xxhash.action is not None
    assert "need its development files in the toolchain image" in xxhash.action
    # The logger stays the C#'s until the retirement; the type it refuses stays C#.
    assert logging.ported
    assert "CodeCoreMorph ships it with the port, and the retirement keeps it" in logging.summary
    assert "the ported code calls back into those the C# passes it" in logging.summary
    assert "Zoo.Scoped stay C# for reasons of their own" in logging.summary
    assert logging.used_by == ["Zoo.Scoped", "Zoo.Store"]


def test_a_test_files_packages_are_its_translations(fake_project: FakeProject) -> None:
    project = _stash(fake_project).load()
    assert mapped_packages_in(project, {"tests/Zoo.Tests/ListLogger.cs"}, "cpp") == [LOGGING]
    assert mapped_packages_in(project, {"tests/Zoo.Tests/ListLogger.cs"}, "rust") == []
    assert mapped_packages_in(project, {"src/Zoo/Other.cs"}, "cpp") == []
