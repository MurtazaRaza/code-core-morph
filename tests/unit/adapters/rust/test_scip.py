"""rust-analyzer's SCIP index, read without a protobuf dependency (D-75).

``data/stats.scip`` and ``data/shapes.scip`` are what ``rust-analyzer scip`` (1.99.1) wrote for
the Phase 16 spike's two crates: ``stats`` (a module of functions and an owned histogram, with
unit and integration tests) and ``shapes`` (a trait, two implementations, an enum with data, a
generic function and a generic struct).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.rust.scip import (
    ScipError,
    read_index,
    references,
    rust_path,
)

DATA = Path(__file__).with_name("data")
CRATES = {"stats": "stats", "shapes": "shapes"}
RA = "rust-analyzer cargo "  # every symbol starts with the scheme and the package manager


def _index(name: str) -> tuple[dict[str, tuple[str, int, int]], list[tuple[str, str, int]]]:
    defined, found = references(read_index((DATA / name).read_bytes()), CRATES)
    return defined, [(r.file, r.path, r.line) for r in found]


def test_documents_and_definitions() -> None:
    documents = read_index((DATA / "stats.scip").read_bytes())
    assert sorted(d.path for d in documents) == [
        "src/lib.rs",
        "src/stats.rs",
        "src/text.rs",
        "tests/api.rs",
    ]
    defined, _ = _index("stats.scip")
    assert defined["stats::stats::mean"][0] == "src/stats.rs"
    assert defined["stats::stats::Histogram"][0] == "src/stats.rs"
    assert defined["stats::stats::Histogram::add"][0] == "src/stats.rs"  # an impl's method
    assert defined["stats::stats::AddError::OutOfRange"][0] == "src/stats.rs"
    assert defined["stats::text::mean_word_length"][0] == "src/text.rs"
    assert defined["stats::stats::tests::histogram_bins"][0] == "src/stats.rs"


def test_references_cross_modules_and_reach_the_tests() -> None:
    _, found = _index("stats.scip")
    assert ("src/text.rs", "stats::stats::mean", 8) in found
    assert ("src/text.rs", "stats::stats", 3) in found  # `use crate::stats;` is a reference
    assert ("tests/api.rs", "stats::stats::Histogram::add", 9) in found
    assert ("src/lib.rs", "stats::text", 3) in found  # `pub mod text;`
    assert not any("core" in path or "std" in path for _, path, _ in found)  # other packages


def test_trait_implementations_and_generics() -> None:
    defined, found = _index("shapes.scip")
    assert defined["shapes::geo::Shape"][0] == "src/geo.rs"
    assert defined["shapes::geo::Shape::area"][0] == "src/geo.rs"
    # `impl Shape for Circle { fn area }` belongs to Circle, as in the item scanner.
    assert defined["shapes::geo::Circle::area"][0] == "src/geo.rs"
    assert defined["shapes::stack::Stack::push"][0] == "src/stack.rs"  # impl<T> Stack<T>
    assert defined["shapes::geo::Cmd::Move"][0] == "src/geo.rs"
    # A generic function's call sites, without their type arguments.
    assert ("src/stack.rs", "shapes::geo::largest", 11) in found
    assert ("src/stack.rs", "shapes::geo::largest", 12) in found
    assert ("src/stack.rs", "shapes::geo::Circle::area", 11) in found


@pytest.mark.parametrize(
    ("symbol", "path"),
    [
        (RA + "stats 0.1.0 crate/", "stats"),
        (RA + "stats 0.1.0 stats/", "stats::stats"),
        (RA + "stats 0.1.0 stats/impl#[Histogram]new().", "stats::stats::Histogram::new"),
        (RA + "shapes 0.1.0 geo/impl#[Circle][Shape]area().", "shapes::geo::Circle::area"),
        (RA + "shapes 0.1.0 stack/impl#[`Stack<T>`]new().", "shapes::stack::Stack::new"),
        (RA + "my-lib 1.0.0 a/b().", "my_lib::a::b"),
        (RA + "my  lib 1.0.0 a/b().", None),  # a doubled space is a space
        (RA + "stats 0.1.0 stats/mean().(xs)", None),  # a parameter
        (RA + "shapes 0.1.0 stack/Stack#[T]", None),  # a type parameter
        (RA + "core https://github.com/rust-lang/rust/library/core option/Option#", None),
        ("local 3", None),
        (RA + "stats 0.1.0 stats/(", None),  # malformed
    ],
)  # fmt: skip
def test_symbols_map_onto_item_paths(symbol: str, path: str | None) -> None:
    assert rust_path(symbol, {**CRATES, "my-lib": "my_lib"}) == path


def test_a_damaged_file_is_an_error_not_a_crash() -> None:
    data = (DATA / "stats.scip").read_bytes()
    with pytest.raises(ScipError):
        read_index(data[: len(data) // 2])
    with pytest.raises(ScipError):
        read_index(b"\x0f")  # wire type 7
