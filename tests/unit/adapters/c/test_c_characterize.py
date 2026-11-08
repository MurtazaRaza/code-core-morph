"""Characterization tests of a C project with a Makefile (SPEC §7.11, D-74): TAP programs over
CodeCoreMorph's helper, registered by a marked block of the top-level Makefile."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.c import CAdapter, characterize
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import BuildTarget, LanguageConfig, TargetKind

TEST = """\
#include "codecoremorph_tap.h"
#include "wf.h"

/* A new table holds no words. */
static void wf_table_new_is_empty(void)
{
    wf_table *t = NULL;
    CCM_CHECK_INT(wf_table_new(&t), WF_OK);
    CCM_CHECK_UINT(wf_table_size(t), 0);
    wf_table_free(t);
}

static void wf_strerror_names_ok(void)
{
    CCM_CHECK_STR(wf_strerror(WF_OK), "no error");
}

int main(void)
{
    CCM_RUN(wf_table_new_is_empty);
    CCM_RUN(wf_strerror_names_ok);
    return ccm_done();
}
"""


def test_the_cases_and_their_checks() -> None:
    first, second = characterize.cases(TEST)
    assert (first.name, first.line) == ("wf_table_new_is_empty", 5)
    assert [c.text for c in first.checks] == [
        "CCM_CHECK_INT(wf_table_new(&t), WF_OK)",
        "CCM_CHECK_UINT(wf_table_size(t), 0)",
    ]
    assert (second.name, [c.text for c in second.checks]) == (
        "wf_strerror_names_ok",
        ['CCM_CHECK_STR(wf_strerror(WF_OK), "no error")'],
    )


def test_a_dropped_case_leaves_with_its_comment_and_its_run() -> None:
    text = characterize.drop_cases(TEST, ["wf_table_new_is_empty"])
    assert "wf_table_new_is_empty" not in text
    assert "A new table holds no words" not in text
    assert [c.name for c in characterize.cases(text)] == ["wf_strerror_names_ok"]
    assert '#include "wf.h"\n\nstatic void wf_strerror_names_ok(void)' in text
    assert characterize.drop_cases(TEST, []) == TEST


def test_the_block_builds_and_runs_each_program() -> None:
    block = characterize.make_block(
        ["tests/ccm/wf_test.c"], ["libwf.a"], ["include", "src"], ["test"]
    )
    assert block.splitlines() == [
        characterize.BEGIN,
        "CODECOREMORPH_CHARACTERIZATION = tests/ccm/wf_test",
        "tests/ccm/wf_test: tests/ccm/wf_test.c tests/ccm/codecoremorph_tap.h libwf.a",
        "\t$(CC) $(CPPFLAGS) $(CFLAGS) -Itests/ccm -Iinclude -Isrc -o $@ tests/ccm/wf_test.c "
        "libwf.a $(LDFLAGS) $(CODECOREMORPH_RUST_LIBS) $(LDLIBS) $(LIBS)",
        "test: codecoremorph-characterization",
        ".PHONY: codecoremorph-characterization",
        "codecoremorph-characterization: $(CODECOREMORPH_CHARACTERIZATION)",
        "\t-./tests/ccm/wf_test",
        characterize.END,
    ]
    makefile = "all: libwf.a\n\ntest: all\n\t./t\n"
    added = characterize.with_block(makefile, block)
    assert added == makefile + "\n" + block
    assert characterize.with_block(added, block) == added
    assert characterize.with_block(added, None) == makefile
    # The test target is the Makefile's own, not the block's prerequisite line.
    assert characterize.test_targets(added, []) == ["test"]
    assert characterize.test_targets("check:\n\t./t\n", []) == ["check"]
    assert characterize.test_targets("all:\n", ["regress"]) == ["regress"]


def _context(tmp_path: Path, build_system: str = "make") -> AdapterContext:
    from codecoremorph.sandbox.native import NativeExecutor

    for name in ("work", "build", "cache", "logs"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return AdapterContext(
        worktree=tmp_path / "work",
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(language="c", standard="c89", build_system=build_system),
    )


def _index(ctx: AdapterContext) -> None:
    from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

    with IndexStore.open(ctx.cache_dir / INDEX_FILE_NAME) as store:
        store.write(
            files=[
                ("include/wf.h", "c", "0" * 64, "header", 1, 1, "utf-8", "lf"),
                ("src/wf.c", "c", "1" * 64, "source", 1, 0, "utf-8", "lf"),
            ],
            symbols=[],
            references=[],
            dependencies=[("src/wf.c", "include/wf.h", "include")],
        )
        store.set_build_targets(
            [
                BuildTarget(
                    name="wf", kind=TargetKind.LIBRARY, files=["src/wf.c"], artifacts=["libwf.a"]
                )
            ]
        )


def test_registering_and_removing_the_tests(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    makefile = b"all: libwf.a\r\n\r\ncheck: all\r\n\t./t\r\n"
    (ctx.worktree / "Makefile").write_bytes(makefile)
    _index(ctx)
    adapter = CAdapter()
    directory = "tests/codecoremorph_characterization"
    test = adapter.characterization_test_file(directory, "wf")
    assert test == f"{directory}/wf_test.c"
    written = adapter.register_test_files(ctx, directory, [test], ["src/wf.c"])
    assert written == ["Makefile", f"{directory}/codecoremorph_tap.h"]
    helper = (ctx.worktree / directory / "codecoremorph_tap.h").read_text(encoding="utf-8")
    assert "static int ccm_done(void)" in helper
    text = (ctx.worktree / "Makefile").read_bytes().decode("utf-8")
    assert "\r\ncheck: codecoremorph-characterization\r\n" in text  # CRLF kept
    assert f"-o $@ {test} libwf.a" in text
    assert f"-I{directory} -Iinclude " in text
    adapter.register_test_files(ctx, directory, [], [])
    assert (ctx.worktree / "Makefile").read_bytes() == makefile
    assert not (ctx.worktree / directory / "codecoremorph_tap.h").exists()


def test_the_cases_ids_are_known_before_they_run(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    path = "tests/codecoremorph_characterization/wf_test.c"
    (ctx.worktree / path).parent.mkdir(parents=True)
    (ctx.worktree / path).write_text(TEST, encoding="utf-8")
    adapter = CAdapter()
    found = adapter.extract_assertions(ctx, [path])
    program = "tap:tests/codecoremorph_characterization/wf_test"
    assert [(a.test_id, len(a.assertions)) for a in found] == [
        (f"{program}/wf_table_new_is_empty", 2),
        (f"{program}/wf_strerror_names_ok", 1),
    ]
    assert adapter.drop_tests(ctx, path, [f"{program}/wf_strerror_names_ok"]) == 1
    assert "wf_strerror_names_ok" not in (ctx.worktree / path).read_text(encoding="utf-8")


def test_other_builds_refuse(tmp_path: Path) -> None:
    adapter = CAdapter()
    with pytest.raises(ConfigError, match="need a Make build"):
        adapter.register_test_files(_context(tmp_path, "cmake"), "t", ["t/x_test.c"], [])
    automake = _context(tmp_path / "am")
    (automake.worktree / "Makefile.am").write_text("SUBDIRS = src\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="automake build"):
        adapter.register_test_files(automake, "t", ["t/x_test.c"], [])
    # Once a port's retirement deleted the Makefile, there is nothing to take out.
    assert adapter.register_test_files(automake, "t", [], []) == ["t/codecoremorph_tap.h"]
