"""What a .NET package's API is in a port from C# (SPEC §11.11, D-95): the mappings CodeCoreMorph
ships (``packages.toml``), read when the C# index of a port's source records the packages its
projects restored, so the planner sees each one's counterpart in the port's target, and when a
target's scaffold gives a unit's new code what its counterparts need (a crate's pin, a header
list, a runtime).

A mapping covers a range of the package's versions, the ones it was checked on, and part of its
API, as the C# index names a use (``K4os.Compression.LZ4.LZ4Codec.Encode``): a use outside it
keeps the type that makes it from the port, by site.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any, cast

from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import PackageCounterpart

DATA_FILE = "packages.toml"


@dataclass(frozen=True)
class Crate:
    """A registry crate a counterpart depends on, pinned by its checksum (Cargo.lock's)."""

    name: str
    version: str
    checksum: str
    features: tuple[str, ...] = ()
    default_features: bool = False

    @property
    def pin(self) -> str:
        """``xxhash-rust 0.8.21``."""
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class TargetMapping:
    counterpart: str
    libraries: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    crates: tuple[Crate, ...] = ()
    runtime: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageMapping:
    name: str
    versions: str
    checked: str
    covers: tuple[str, ...]
    excepted: tuple[str, ...]
    notes: tuple[str, ...]
    targets: Mapping[str, TargetMapping] = field(default_factory=dict[str, TargetMapping])


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in cast(list[object], value))


def _crate(entry: Mapping[str, Any]) -> Crate:
    checksum = str(entry.get("checksum", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ConfigError(
            f"the package mapping's crate {entry.get('name')!r} has no SHA-256 checksum",
            hint="Each crate a mapping pins carries Cargo.lock's checksum of it (SPEC §7.15).",
        )
    return Crate(
        name=str(entry["name"]),
        version=str(entry["version"]),
        checksum=checksum,
        features=_strings(entry.get("features")),
        default_features=bool(entry.get("default_features", False)),
    )


def _target(entry: Mapping[str, Any]) -> TargetMapping:
    crates = cast(list[Mapping[str, Any]], entry.get("crates") or [])
    runtime = entry.get("runtime")
    return TargetMapping(
        counterpart=str(entry["counterpart"]),
        libraries=_strings(entry.get("libraries")),
        headers=_strings(entry.get("headers")),
        crates=tuple(_crate(c) for c in crates),
        runtime=str(runtime) if runtime else None,
        notes=_strings(entry.get("notes")),
    )


def parse(text: str) -> dict[str, PackageMapping]:
    """The mappings of a ``packages.toml``, by package name."""
    data = tomllib.loads(text)
    found: dict[str, PackageMapping] = {}
    packages = cast(Mapping[str, Mapping[str, Any]], data.get("packages") or {})
    for name, entry in packages.items():
        targets = cast(Mapping[str, Mapping[str, Any]], entry.get("targets") or {})
        found[name] = PackageMapping(
            name=name,
            versions=str(entry.get("versions", "")),
            checked=str(entry.get("checked", "")),
            covers=_strings(entry.get("covers")),
            excepted=_strings(entry.get("except")),
            notes=_strings(entry.get("notes")),
            targets={t: _target(m) for t, m in targets.items()},
        )
    return found


@cache
def mappings() -> dict[str, PackageMapping]:
    """The mappings CodeCoreMorph ships, by package name."""
    text = (resources.files("codecoremorph.adapters.csharp") / DATA_FILE).read_text("utf-8")
    return parse(text)


def _numbers(version: str) -> tuple[int, ...]:
    """``1.3.8``, ``8.0.2-preview.1`` and ``1.3`` as numbers (a pre-release as its release)."""
    core = re.split(r"[-+]", version.strip(), maxsplit=1)[0]
    parts = [int(p) for p in re.findall(r"\d+", core)]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def in_range(version: str, spec: str) -> bool:
    """Whether ``version`` satisfies ``spec``: comma-separated ``>=``, ``>``, ``<=``, ``<``,
    ``==`` bounds, as NuGet's versions compare (``>=1.1, <2``). No version satisfies a spec."""
    if not version.strip():
        return False
    have = _numbers(version)
    for bound in (b.strip() for b in spec.split(",")):
        if not bound:
            continue
        match = re.fullmatch(r"(>=|<=|==|>|<)\s*([0-9][0-9.]*)", bound)
        if match is None:
            raise ConfigError(
                f"the package mapping's version range {spec!r} has a bound it can't read",
                hint="Write bounds as >=1.1, <2.",
            )
        op, want = match.group(1), _numbers(match.group(2))
        ok = {
            ">=": have >= want,
            ">": have > want,
            "<=": have <= want,
            "<": have < want,
            "==": have == want,
        }[op]
        if not ok:
            return False
    return True


def counterpart(name: str, version: str, target: str) -> PackageCounterpart | None:
    """The counterpart in ``target`` (a language ID) of the package ``name`` at ``version``,
    when CodeCoreMorph maps that version of it there."""
    mapping = mappings().get(name)
    if mapping is None or target not in mapping.targets:
        return None
    if not in_range(version, mapping.versions):
        return None
    on = mapping.targets[target]
    return PackageCounterpart(
        target=target,
        counterpart=on.counterpart,
        checked=mapping.checked,
        covers=list(mapping.covers),
        excepted=list(mapping.excepted),
        libraries=list(on.libraries),
        headers=list(on.headers),
        crates=[c.pin for c in on.crates],
        runtime=on.runtime,
        notes=[*mapping.notes, *on.notes],
    )


def target_mapping(package: str, target: str) -> TargetMapping | None:
    """What a target's scaffold needs of a package's counterpart."""
    mapping = mappings().get(package)
    return mapping.targets.get(target) if mapping is not None else None
