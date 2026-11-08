"""A Rust crate as a port's source (D-75): references from SCIP onto the scanner's items, and
the crate's targets and tests. ``data/stats`` is the crate ``data/stats.scip`` indexes."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.rust.scip import read_index
from codecoremorph.adapters.rust.source import (
    SourceIndex,
    add_references,
    add_targets_and_tests,
    file_role,
    files_with_tests,
)
from codecoremorph.adapters.rust.symbols import scan_crate
from codecoremorph.core.models import FileRole, TargetKind

DATA = Path(__file__).with_name("data")
CRATE = DATA / "stats"


def _files() -> dict[str, str]:
    return {
        p.relative_to(CRATE).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(CRATE.rglob("*.rs"))
    }


def _known(prefix: str) -> dict[str, str]:
    sources = {rel: (prefix + rel, text) for rel, text in _files().items()}
    return {item.path: item.file for item in scan_crate("stats", sources)}


def test_roles() -> None:
    assert file_role("tests/api.rs") is FileRole.TEST
    assert file_role("tests/common/mod.rs") is FileRole.TEST
    assert file_role("build.rs") is FileRole.BUILD
    assert file_role("Cargo.toml") is FileRole.BUILD
    assert file_role("src/stats.rs") is FileRole.SOURCE  # its #[cfg(test)] tests too
    assert file_role("src/main.rs") is FileRole.SOURCE


def test_references_tie_the_files_by_what_they_use() -> None:
    out = SourceIndex()
    documents = read_index((DATA / "stats.scip").read_bytes())
    crates = {"stats": "stats"}
    add_references(
        out, documents, package="lib", crate="stats", crates=crates, known=_known("lib/")
    )
    # A file's module, which the scanner doesn't list, becomes a symbol of that file.
    modules = {row[2]: row[4] for row in out.symbols}
    assert modules == {"stats::stats": "lib/src/stats.rs", "stats::text": "lib/src/text.rs"}
    edges = {(row[0], row[2]) for row in out.references}
    assert ("lib/src/text.rs", "rust:stats::stats::mean") in edges
    assert ("lib/src/text.rs", "rust:stats::stats") in edges  # use crate::stats;
    assert ("lib/src/lib.rs", "rust:stats::text") in edges  # pub mod text;
    assert ("lib/tests/api.rs", "rust:stats::stats::Histogram::new") in edges
    # Every reference is to an item the index has: the scanner's, or a file's module.
    known = set(_known("lib/")) | set(modules)
    assert all(row[2].removeprefix("rust:") in known for row in out.references)


def test_targets_and_tests() -> None:
    out = SourceIndex()
    manifest = (CRATE / "Cargo.toml").read_text(encoding="utf-8")
    add_targets_and_tests(out, package=".", name="stats", manifest=manifest, files=_files())
    targets = {t.name: t for t in out.targets}
    assert targets["stats"].kind is TargetKind.LIBRARY
    assert targets["stats"].files == ["src/lib.rs", "src/stats.rs", "src/text.rs"]
    assert targets["stats::api"].kind is TargetKind.EXECUTABLE
    assert targets["stats::api"].files == ["tests/api.rs"]
    assert targets["stats::api"].dependencies == ["stats"]
    assert files_with_tests(out.tests) == {
        "src/stats.rs": [
            "nextest:stats::stats::tests::mean_of_three",
            "nextest:stats::stats::tests::median_even_and_odd",
            "nextest:stats::stats::tests::histogram_bins",
        ],
        "src/text.rs": ["nextest:stats::text::tests::words"],
        "tests/api.rs": ["nextest:stats::api::the_public_api"],
    }
    assert {t.runs for t in out.tests} == {"stats", "stats::api"}


def test_programs_are_targets_of_their_own() -> None:
    out = SourceIndex()
    files = {
        "src/lib.rs": "pub fn f() {}\n",
        "src/main.rs": "fn main() { tool::f(); }\n",
        "src/bin/other.rs": "fn main() {}\n",
    }
    add_targets_and_tests(
        out, package="tool", name="tool", manifest='[package]\nname = "tool"\n', files=files
    )
    # A package's library and its src/main.rs program share a name: programs are named as
    # nextest names their binaries, and their artifact is the program's name.
    targets = {t.name: (t.kind, t.files, t.artifacts) for t in out.targets}
    assert targets == {
        "tool": (TargetKind.LIBRARY, ["tool/src/lib.rs"], []),
        "tool::bin/tool": (TargetKind.EXECUTABLE, ["tool/src/main.rs"], ["tool"]),
        "tool::bin/other": (TargetKind.EXECUTABLE, ["tool/src/bin/other.rs"], ["other"]),
    }


def test_a_workspace_program_uses_its_library() -> None:
    """The round trip's workspace (a C → Rust port's ``rust/``): the program's package depends
    on the library's by path, its target says so, and its SCIP index (``data/wordfreq.scip``,
    rust-analyzer run in the member's directory) ties its files to the library's items."""
    out = SourceIndex()
    manifest = (
        '[package]\nname = "wordfreq_rs"\n\n[[bin]]\nname = "wordfreq"\npath = "src/main.rs"\n\n'
        '[dependencies]\nwf_rs = { path = "../wf_rs" }\n'
    )
    files = {"src/main.rs": "fn main() {}\n", "tests/cli.rs": "#[test]\nfn runs() {}\n"}
    add_targets_and_tests(
        out,
        package="rust/wordfreq_rs",
        name="wordfreq_rs",
        manifest=manifest,
        files=files,
        local={"wf_rs", "wordfreq_rs"},
    )
    targets = {t.name: (t.files, t.dependencies) for t in out.targets}
    assert targets == {
        "wordfreq_rs::bin/wordfreq": (["rust/wordfreq_rs/src/main.rs"], ["wf_rs"]),
        "wordfreq_rs::cli": (["rust/wordfreq_rs/tests/cli.rs"], ["wf_rs"]),
    }
    assert [t.test_id for t in out.tests] == ["nextest:wordfreq_rs::cli::runs"]
    documents = read_index((DATA / "wordfreq.scip").read_bytes())
    known = {
        "wf_rs::wf::Table::new": "rust/wf_rs/src/wf.rs",
        "wf_rs::wf::Table::top": "rust/wf_rs/src/wf.rs",
        "wf_rs::conf::set_fold_case": "rust/wf_rs/src/conf.rs",
        "wordfreq_rs::usage": "rust/wordfreq_rs/src/main.rs",
    }
    refs = SourceIndex()
    add_references(
        refs,
        documents,
        package="rust/wordfreq_rs",
        crate="wordfreq_rs",
        crates={"wf_rs": "wf_rs", "wordfreq_rs": "wordfreq_rs"},
        known=known,
    )
    edges = {(row[0], row[2]) for row in refs.references}
    assert ("rust/wordfreq_rs/src/main.rs", "rust:wf_rs::wf::Table::new") in edges
    assert ("rust/wordfreq_rs/src/main.rs", "rust:wf_rs::conf::set_fold_case") in edges
    assert ("rust/wordfreq_rs/src/main.rs", "rust:wordfreq_rs::usage") in edges
