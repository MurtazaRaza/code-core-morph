"""Which files clang-tidy looks at for a unit's diagnostics (D-83): the unit's own sources, and
for a header, its own source or a file that includes it; everything when that can't be told."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.cpp.tidy import lint_scope

COMPILED = [
    "cpp/bridge/box.cpp",
    "cpp/src/box.cpp",
    "cpp/src/key.cpp",
    "cpp/tests/box_test.cpp",
]


def _tree(root: Path) -> Path:
    files = {
        "cpp/bridge/box.cpp": '#include "../src/box.hpp"\n#include "box.h"\n',
        "cpp/src/box.cpp": '#include "box.hpp"\n',
        "cpp/src/key.cpp": '#include "key.hpp"\n#include "detail/bytes.hpp"\n',
        "cpp/tests/box_test.cpp": '#include <gtest/gtest.h>\n#include "box.hpp"\n',
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_all_of_the_port_without_files(tmp_path: Path) -> None:
    assert lint_scope(_tree(tmp_path), COMPILED, None) == COMPILED


def test_a_type_units_sources(tmp_path: Path) -> None:
    unit = {"cpp/src/box.hpp", "cpp/src/box.cpp", "cpp/bridge/box.h", "cpp/bridge/box.cpp"}
    assert lint_scope(_tree(tmp_path), COMPILED, unit) == ["cpp/bridge/box.cpp", "cpp/src/box.cpp"]


def test_a_test_units_file(tmp_path: Path) -> None:
    unit = {"cpp/tests/box_test.cpp", "cpp/tests/CMakeLists.txt", "cpp/test-map.toml"}
    assert lint_scope(_tree(tmp_path), COMPILED, unit) == ["cpp/tests/box_test.cpp"]


def test_a_header_alone_brings_its_own_source(tmp_path: Path) -> None:
    assert lint_scope(_tree(tmp_path), COMPILED, {"cpp/src/key.hpp"}) == ["cpp/src/key.cpp"]


def test_a_header_without_a_source_brings_a_file_that_includes_it(tmp_path: Path) -> None:
    unit = {"cpp/src/detail/bytes.hpp"}
    assert lint_scope(_tree(tmp_path), COMPILED, unit) == ["cpp/src/key.cpp"]


def test_a_header_nothing_includes_lints_everything(tmp_path: Path) -> None:
    """Whatever includes it does so in a way the ``#include`` lines don't show: all of it,
    as before."""
    assert lint_scope(_tree(tmp_path), COMPILED, {"cpp/src/orphan.hpp"}) == COMPILED
