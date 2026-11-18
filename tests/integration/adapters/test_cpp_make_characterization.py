"""C++ characterization tests in a Make build (D-89), with the real toolchain: GoogleTest
programs from a block at the end of the Makefile, built before the test target's recipe and
linked with the library's archive and the toolchain's GoogleTest (for C++98 the release that
supports it, D-38); each writes GoogleTest XML, so a case that holds passes and one that
doesn't fails by its ``gtest:`` ID, and the project's own test still runs. Marked ``docker``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import LanguageConfig, TestStatus

pytestmark = pytest.mark.docker

DIRECTORY = "tests/codecoremorph_characterization"


def _project(std: str) -> dict[str, str]:
    return {
        # -c in CXXFLAGS, as some old Makefiles have: the block's rule takes it out.
        "Makefile": (
            f"CXXFLAGS = -std={std} -Wall -Iinclude\n\n"
            "all: libcounter.a\n\n"
            "libcounter.a: src/counter.o\n\t$(AR) rcs $@ src/counter.o\n\n"
            "src/counter.o: src/counter.cpp include/counter.h\n"
            "\t$(CXX) $(CXXFLAGS) -c -o $@ src/counter.cpp\n\n"
            "check: all\n"
            "\t$(CXX) $(CXXFLAGS) -o tests/smoke tests/smoke.cpp libcounter.a\n"
            "\t./tests/smoke\n"
        ),
        "include/counter.h": (
            "#ifndef COUNTER_H\n#define COUNTER_H\nclass Counter {\npublic:\n"
            "  Counter() : n_(0) {}\n  void add(int k);\n  int value() const { return n_; }\n"
            "private:\n  int n_;\n};\n#endif\n"
        ),
        "src/counter.cpp": '#include "counter.h"\nvoid Counter::add(int k) { n_ += k; }\n',
        "tests/smoke.cpp": (
            '#include "counter.h"\nint main() { Counter c; c.add(2); return c.value() == 2 ? 0 '
            ": 1; }\n"
        ),
    }


CASES = """\
#include <gtest/gtest.h>
#include "counter.h"

TEST(CounterCharacterization, AddsWhatItIsGiven) {
  Counter c;
  c.add(3);
  EXPECT_EQ(3, c.value());
}

TEST(CounterCharacterization, AWrongValueFails) {
  Counter c;
  EXPECT_EQ(1, c.value());
}
"""


@pytest.fixture
def make_ctx(tmp_path: Path) -> Iterator[object]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    executors: list[DockerExecutor] = []

    def make(files: dict[str, str], standard: str) -> AdapterContext:
        lang = LanguageConfig(
            language="cpp",
            standard=standard,
            build_system="make",
            test_dirs=["tests"],
            build_timeout_s=900,
            test_timeout_s=300,
        )
        try:
            image = resolve_image(CppAdapter().toolchain_requirements(lang), None)
        except SandboxError as exc:
            pytest.skip(f"no toolchain image: {exc}")
        work = tmp_path / "work"
        for rel, text in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(text.encode("utf-8"))
        for name in ("build", "cache", "logs"):
            (tmp_path / name).mkdir()
        executor = DockerExecutor(
            DockerSettings(
                image=image,
                run_id=new_run_id(),
                worktree=work,
                build_dir=tmp_path / "build",
                cache_dir=tmp_path / "cache",
                log_dir=tmp_path / "exec",
                toolchain_env="/opt/codecoremorph/toolchains/cpp.env",
            )
        )
        executor.start()
        executors.append(executor)
        return AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=lang,
        )

    yield make
    for executor in executors:
        executor.stop()


@pytest.mark.parametrize(("standard", "std"), [("cpp11", "c++11"), ("cpp98", "c++98")])
def test_a_make_projects_characterization_tests(make_ctx, standard: str, std: str) -> None:  # type: ignore[no-untyped-def]
    ctx: AdapterContext = make_ctx(_project(std), standard)
    cpp = CppAdapter()
    assert cpp.configure(ctx).success
    assert cpp.build(ctx).success
    cpp.index(ctx)
    test = cpp.characterization_test_file(DIRECTORY, "counter")
    (ctx.worktree / test).parent.mkdir(parents=True, exist_ok=True)
    (ctx.worktree / test).write_text(CASES, encoding="utf-8")
    assert cpp.characterization_unavailable(ctx) is None
    assert cpp.register_test_files(ctx, DIRECTORY, [test], ["src/counter.cpp"]) == ["Makefile"]
    assert cpp.configure(ctx).success
    built = cpp.build(ctx)
    assert built.success, built.diagnostics
    statuses = {t.test_id: t.status for t in cpp.run_tests(ctx).cases}
    assert statuses["gtest:CounterCharacterization.AddsWhatItIsGiven"] is TestStatus.PASSED
    assert statuses["gtest:CounterCharacterization.AWrongValueFails"] is TestStatus.FAILED
    assert "make:check" in statuses  # the project's own test still runs
