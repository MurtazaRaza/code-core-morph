"""The characterization tests are tests whatever the manifest's test globs say (D-46, D-62).

snappy's manifest names its tests by file (`snappy_unittest.cc`, `snappy-test.*`), so the
tests CodeCoreMorph wrote under tests/codecoremorph_characterization/ were classified as
program code, and a port planned them as a Rust program instead of translating them.
"""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.config import language_config, target_language_config
from codecoremorph.adapters.cpp.indexing import FileClassifier
from codecoremorph.config import parse_config
from codecoremorph.core.models import FileRole

WRITTEN = "tests/codecoremorph_characterization"
PORT = parse_config(
    'version: "1.0"\n'
    'project: {name: demo, test_dirs: ["snappy_unittest.cc", "snappy-test.*"]}\n'
    "migration:\n"
    "  source: {language: cpp, standard: cpp11}\n"
    "  target: {language: rust, standard: rust2021, rules: [port_module]}\n"
).config


def test_the_written_tests_join_the_project_tests() -> None:
    assert PORT.project.test_dirs == ["snappy_unittest.cc", "snappy-test.*"]
    assert PORT.test_dirs == ["snappy_unittest.cc", "snappy-test.*", WRITTEN]
    assert language_config(PORT).test_dirs == PORT.test_dirs
    assert target_language_config(PORT).test_dirs == PORT.test_dirs


def test_no_twice_and_another_directory() -> None:
    covered = parse_config(
        'version: "1.0"\n'
        f'project: {{name: demo, test_dirs: [tests, "{WRITTEN}"]}}\n'
        "migration:\n"
        "  source: {language: cpp, standard: cpp11}\n"
        "  target: {language: cpp, standard: cpp20}\n"
        "parity: {characterization_tests: {dir: checks/written}}\n"
    ).config
    assert covered.test_dirs == ["tests", WRITTEN, "checks/written"]


def test_the_cpp_index_calls_them_tests(tmp_path: Path) -> None:
    for rel in (f"{WRITTEN}/snappy_test.cpp", "snappy_unittest.cc", "snappy.cc"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("int f() { return 0; }\n", encoding="utf-8")
    ctx = AdapterContext(
        worktree=tmp_path,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=None,  # type: ignore[arg-type]  # classifying files runs no command
        lang=language_config(PORT),
    )
    classifier = FileClassifier(ctx)

    def role(rel: str) -> str:
        row = classifier.row(rel, c_compatible=False)
        assert row is not None
        return row[3]

    assert role(f"{WRITTEN}/snappy_test.cpp") == FileRole.TEST.value
    assert role("snappy_unittest.cc") == FileRole.TEST.value
    assert role("snappy.cc") == FileRole.SOURCE.value
