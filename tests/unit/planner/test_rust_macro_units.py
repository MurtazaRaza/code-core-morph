"""A port from Rust of a crate with procedural macros of its own (D-93), planned over the Rust
adapter's real index of ``fixtures/rust/wire`` (rust-analyzer's SCIP output and expansions,
recorded): the macro package is no library to port; a module's exports take in what its
macros write; the notes name the sites; the macro package's tests get no test unit; the
retirement takes the package with the Rust. A macro the crate re-exports, and a site without
an expansion, stay refused."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.adapters.rust.scip import Document, read_index
from codecoremorph.core.models import (
    ExecResult,
    LanguageConfig,
    MacroKind,
    MacroSite,
    UnitStatus,
    WorkUnit,
)
from codecoremorph.planner.project import ProjectModel
from codecoremorph.store.index import IndexStore

from .test_rust_units import _plan  # pyright: ignore[reportPrivateUsage]

ROOT = Path(__file__).resolve().parents[3]
WIRE = ROOT / "fixtures" / "rust" / "wire"
DATA = ROOT / "tests" / "unit" / "adapters" / "rust" / "data"


class _Executor:
    """Answers the expansion helper with rust-analyzer's recorded expansions."""

    kind = "native"
    toolchain_env: Mapping[str, str] = {}

    def to_sandbox_path(self, host_path: Path) -> str:
        return str(host_path)

    def run(self, argv: Sequence[str], **_: Any) -> ExecResult:
        Path(argv[4]).write_bytes((DATA / "wire-expansions.json").read_bytes())
        return ExecResult(
            argv=list(argv), exit_code=0, stdout="", stderr="", duration_s=0, log_path=Path("x")
        )


def _index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit: dict[str, str] | None = None
) -> tuple[ProjectModel, AdapterContext]:
    work = tmp_path / "wire"
    shutil.copytree(WIRE, work)
    for rel, text in (edit or {}).items():
        (work / rel).write_text(text, encoding="utf-8")
    adapter = RustAdapter()

    def scip(ctx: AdapterContext, package: str, problems: list[str]) -> list[Document]:
        name = "wire_macros" if package == "macros" else "wire"
        return read_index((DATA / f"{name}.scip").read_bytes())

    monkeypatch.setattr(adapter, "_scip", scip)
    executor: Any = _Executor()
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,
        lang=LanguageConfig(language="rust", standard="rust2018", port_target="c"),
    )
    index = adapter.index(ctx)
    assert not index.problems
    return ProjectModel.load(index.path, work, test_dirs=["tests"]), ctx


def _by_file(units: list[WorkUnit]) -> dict[tuple[str, str], WorkUnit]:
    """Each module or test unit by its Rust file (a module's unit has the crate root too)."""
    found: dict[tuple[str, str], WorkUnit] = {}
    for unit in units:
        if unit.rules[0] == "retire_rust":
            continue
        code = [f for f in unit.files if f.endswith(".rs")]
        found[(unit.rules[0], ([f for f in code if f != "src/lib.rs"] or code)[0])] = unit
    return found


def test_a_crate_with_procedural_macros_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, ctx = _index(tmp_path, monkeypatch)
    plan = _plan(project, ctx)
    assert all(u.status is UnitStatus.PENDING for u in plan.units)
    modules = [u for u in plan.units if u.rules[0] == "port_rust_module"]
    # codec and crc first: msg uses both. The macro package is no module of the port.
    assert [u.port.module for u in modules if u.port] == ["codec", "crc", "msg"]
    assert not any("macros/" in f for u in modules for f in u.files if f.endswith(".rs"))
    units = _by_file(plan.units)
    msg = units[("port_rust_module", "src/msg.rs")]
    assert msg.port is not None
    # What the derives write is msg's to map, with its own items.
    assert {
        "wire::msg::Header::encode",
        "wire::msg::Header::encoded_len",
        "wire::msg::Header::decode",
        "wire::msg::Command::decode",
        "wire::msg::read_frame",
        "wire::msg::write_frame",
    } <= set(msg.port.exports)
    assert msg.note is not None
    assert "8 sites of the crate's own procedural macros" in msg.note
    assert "Encode at line 7" in msg.note
    crc = units[("port_rust_module", "src/crc.rs")]
    assert crc.note is not None
    assert "1 site of the crate's own procedural macros (crc_table at line 5)" in crc.note
    codec = units[("port_rust_module", "src/codec.rs")]
    assert codec.note is not None
    assert "procedural macros" not in codec.note
    frames = units[("port_rust_tests", "tests/frames.rs")]
    assert frames.note is not None
    assert "2 sites of the crate's own procedural macros" in frames.note
    # A module's own tests have none: its sites are the module unit's.
    msg_tests = units[("port_rust_tests", "src/msg.rs")]
    assert "procedural macros" not in (msg_tests.note or "")
    # The macro package's own test is no test unit's: it goes with the package.
    tests = [u for u in plan.units if u.rules[0] == "port_rust_tests"]
    assert not any(t.startswith("nextest:wire_macros") for u in tests for t in u.port.tests)  # type: ignore[union-attr]
    retire = plan.units[-1]
    assert retire.rules == ["retire_rust"]
    assert {"macros/Cargo.toml", "macros/src/lib.rs"} <= set(retire.files)
    assert retire.note is not None
    assert "the procedural macro package (macros) goes with it, and its tests" in retire.note


def _with_sites(project: ProjectModel, change: Any) -> ProjectModel:
    with IndexStore.open(project.index_path) as store:
        sites: list[MacroSite] = change(store.macro_sites())
        store.set_macro_sites(sites)
    return ProjectModel.load(project.index_path, project.worktree, test_dirs=["tests"])


def test_a_site_without_an_expansion_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, ctx = _index(tmp_path, monkeypatch)

    def drop(sites: list[MacroSite]) -> list[MacroSite]:
        return [
            s.model_copy(update={"expansion": None, "items": []})
            if (s.file, s.line) == ("src/msg.rs", 42)
            else s
            for s in sites
        ]

    plan = _plan(_with_sites(project, drop), ctx)
    units = _by_file(plan.units)
    msg = units[("port_rust_module", "src/msg.rs")]
    assert msg.status is UnitStatus.SKIPPED
    assert msg.note is not None
    assert "no expansion for (throws at line 42)" in msg.note
    assert units[("port_rust_module", "src/codec.rs")].status is UnitStatus.PENDING


def test_a_site_in_a_modules_tests_is_its_test_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test-only type's derive in a module's ``#[cfg(test)]`` tests stays Rust with them:
    the module's unit neither ports nor maps it, its test unit does."""
    project, ctx = _index(tmp_path, monkeypatch)

    def in_tests(sites: list[MacroSite]) -> list[MacroSite]:
        extra = MacroSite(
            file="src/codec.rs",
            line=96,
            column=14,
            macro="Encode",
            kind=MacroKind.DERIVE,
            package="wire_macros",
            expansion="impl ::wire::codec::Encode for Probe {}",
            test_only=True,
        )
        return [*sites, extra]

    units = _by_file(_plan(_with_sites(project, in_tests), ctx).units)
    codec = units[("port_rust_module", "src/codec.rs")]
    assert codec.status is UnitStatus.PENDING
    assert "procedural macros" not in (codec.note or "")
    tests = units[("port_rust_tests", "src/codec.rs")]
    assert "1 site of the crate's own procedural macros (Encode at line 96)" in (tests.note or "")


def test_a_re_exported_macro_keeps_the_rust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pub use wire_macros::Encode;`` in a module hands the derive to code outside the crate,
    which C can't: that module is refused; in the crate root, the retirement."""
    project, ctx = _index(tmp_path, monkeypatch)

    def reexport(file: str) -> Any:
        def change(sites: list[MacroSite]) -> list[MacroSite]:
            extra = MacroSite(
                file=file,
                line=3,
                column=22,
                macro="Encode",
                kind=MacroKind.DERIVE,
                package="wire_macros",
                reexport=True,
            )
            return [*sites, extra]

        return change

    plan = _plan(_with_sites(project, reexport("src/codec.rs")), ctx)
    codec = _by_file(plan.units)[("port_rust_module", "src/codec.rs")]
    assert codec.status is UnitStatus.SKIPPED
    assert codec.note is not None
    assert "re-exports procedural macros of the crate's own, Encode (src/codec.rs:3)" in codec.note
    project, ctx = _index(tmp_path / "root", monkeypatch)
    plan = _plan(_with_sites(project, reexport("src/lib.rs")), ctx)
    retire = plan.units[-1]
    assert retire.rules == ["retire_rust"]
    assert retire.status is UnitStatus.SKIPPED
    assert "the Rust stays" in (retire.note or "")


def test_the_recorded_expansions_are_the_fixtures() -> None:
    """The recorded data matches the fixture: each site's line still holds its macro."""
    expansions = json.loads((DATA / "wire-expansions.json").read_text(encoding="utf-8"))
    for key in expansions:
        if key.startswith("_"):
            continue
        file, line, _ = key.rsplit(":", 2)
        text = (WIRE / file).read_text(encoding="utf-8").splitlines()[int(line) - 1]
        assert any(m in text for m in ("Encode", "Decode", "throws", "crc_table")), key
