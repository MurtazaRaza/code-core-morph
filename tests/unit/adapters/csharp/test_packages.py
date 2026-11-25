"""The .NET packages CodeCoreMorph maps to a counterpart in a port's target (D-95): the shipped
mappings, the versions each covers, the API it covers, and what a target's scaffold reads of
it."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.cpp import csharpports as cpp
from codecoremorph.adapters.csharp import packages
from codecoremorph.adapters.rust import csharpports as rust
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import PackageCounterpart

LZ4 = "K4os.Compression.LZ4"
XXHASH = "K4os.Hash.xxHash"
LOGGING = "Microsoft.Extensions.Logging.Abstractions"


def test_the_shipped_mappings_cover_both_targets() -> None:
    mappings = packages.mappings()
    assert {LZ4, XXHASH, LOGGING} <= set(mappings)
    for mapping in mappings.values():
        assert set(mapping.targets) == {"cpp", "rust"}, mapping.name
        assert mapping.covers, mapping.name
        assert mapping.notes, mapping.name
        assert mapping.checked, mapping.name
        for target, on in mapping.targets.items():
            # A counterpart is a C library, crates or a runtime the scaffolds know.
            assert on.libraries or on.crates or on.runtime, (mapping.name, target)
            if on.libraries:
                assert on.headers, (mapping.name, target)
            if on.runtime is not None:
                known = cpp.PACKAGE_RUNTIMES if target == "cpp" else rust.PACKAGE_RUNTIMES
                assert on.runtime in known, (mapping.name, target)


def test_a_counterpart_for_the_versions_checked() -> None:
    found = packages.counterpart(LZ4, "1.3.8", "cpp")
    assert found is not None
    assert found.counterpart == "liblz4"
    assert found.libraries == ["lz4"]
    assert found.headers == ["lz4.h", "lz4hc.h"]
    assert "byte for byte" in found.checked
    assert any("no block at all" in n for n in found.notes)
    # A version outside the range, a target without a mapping, a package without one.
    assert packages.counterpart(LZ4, "2.0.0", "cpp") is None
    assert packages.counterpart(LZ4, "", "cpp") is None
    assert packages.counterpart(LZ4, "1.3.8", "java") is None
    assert packages.counterpart("Newtonsoft.Json", "13.0.3", "cpp") is None


def test_a_crate_counterpart_is_pinned_by_checksum() -> None:
    found = packages.counterpart(XXHASH, "1.0.8", "rust")
    assert found is not None
    assert found.crates == ["xxhash-rust 0.8.21"]
    assert found.libraries == []
    on = packages.target_mapping(XXHASH, "rust")
    assert on is not None
    (crate,) = on.crates
    assert crate.features == ("xxh32", "xxh64")
    assert not crate.default_features
    assert len(crate.checksum) == 64
    with pytest.raises(ConfigError, match="no SHA-256 checksum"):
        packages.parse(
            '[packages.P]\nversions = ">=1"\n[packages.P.targets.rust]\ncounterpart = "c"\n'
            'crates = [{ name = "c", version = "1.0.0" }]\n'
        )


@pytest.mark.parametrize(
    ("version", "spec", "ok"),
    [
        ("1.3.8", ">=1.1, <2", True),
        ("1.1", ">=1.1, <2", True),
        ("1.0.9", ">=1.1, <2", False),
        ("2.0.0", ">=1.1, <2", False),
        ("8.0.2-preview.1", ">=2.0, <11", True),
        ("10.0.0", ">=2.0, <11", True),
        ("3.0.0", "==3", True),
        ("3.0.1", "==3", False),
    ],
)
def test_version_ranges(version: str, spec: str, ok: bool) -> None:
    assert packages.in_range(version, spec) is ok


def test_a_range_it_cant_read() -> None:
    with pytest.raises(ConfigError, match="can't read"):
        packages.in_range("1.0", "~1.0")


def test_what_a_counterpart_covers() -> None:
    found = packages.counterpart(LOGGING, "8.0.2", "rust")
    assert isinstance(found, PackageCounterpart)
    assert found.runtime == "logging"
    # A covered type, its members, its generic form; not what the mapping leaves out, nor what
    # only begins with a covered name.
    assert found.covers_use("Microsoft.Extensions.Logging.ILogger")
    assert found.covers_use("Microsoft.Extensions.Logging.ILogger<Stash.Store>")
    assert found.covers_use("Microsoft.Extensions.Logging.ILogger<Shop.Cart<int>>.IsEnabled")
    assert found.covers_use("Microsoft.Extensions.Logging.LoggerExtensions.LogDebug")
    assert found.covers_use("Microsoft.Extensions.Logging.EventId..ctor")
    assert not found.covers_use("Microsoft.Extensions.Logging.ILogger.BeginScope")
    assert not found.covers_use("Microsoft.Extensions.Logging.ILogger<Stash.Store>.Log")
    assert not found.covers_use("Microsoft.Extensions.Logging.LoggerExtensions.BeginScope")
    assert not found.covers_use("Microsoft.Extensions.Logging.LoggerMessage.Define")
    assert not found.covers_use("Microsoft.Extensions.Logging.ILoggerProvider")
