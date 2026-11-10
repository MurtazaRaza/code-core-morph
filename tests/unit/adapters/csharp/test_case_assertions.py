"""A C# test's assertions by the IDs the test map pairs (I-3, I-4, D-78): with a port's index, a
parameterized method's checks count for each of its cases, by the case IDs its TRX gives; without
one (an upgrade's index has no test methods), and for a method whose cases the sources can't
tell, they stay the method's."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp import CSharpAdapter
from codecoremorph.core.models import LanguageConfig, TestMethod
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.store.index import INDEX_FILE_NAME, IndexStore

SOURCE = """\
using NUnit.Framework;

namespace Shop.Tests
{
    public class MoneyTests
    {
        [Test]
        public void Adds()
        {
            Assert.AreEqual(3, 1 + 2);
        }

        [TestCase(1, 2)]
        [TestCase(2, 4)]
        public void Doubles(int n, int expected)
        {
            Assert.AreEqual(expected, n * 2);
            Assert.That(n, Is.Positive);
        }

        [TestCaseSource("Rows")]
        public void FromRows(int n)
        {
            Assert.That(n, Is.Positive);
        }
    }
}
"""
FILE = "tests/Shop.Tests/MoneyTests.cs"
STEM = "dotnet:Shop.Tests.MoneyTests#"


def _method(name: str, cases: list[str], *, exact: bool = True) -> TestMethod:
    return TestMethod(
        file=FILE,
        class_name="Shop.Tests.MoneyTests",
        method=name,
        framework="nunit",
        cases=cases,
        exact=exact,
    )


def _counts(ctx: AdapterContext) -> dict[str, int]:
    found = CSharpAdapter().extract_assertions(ctx, [FILE, "README.md"])
    return {a.test_id: len(a.assertions) for a in found}


def test_a_parameterized_methods_checks_count_for_each_case(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "tests" / "Shop.Tests").mkdir(parents=True)
    (work / FILE).write_text(SOURCE, encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=cache,
        log_dir=tmp_path / "logs",
        executor=NativeExecutor(log_dir=tmp_path / "exec"),
        lang=LanguageConfig(language="csharp", standard="csharp7.3"),
    )
    by_method = {f"{STEM}Adds": 1, f"{STEM}Doubles": 2, f"{STEM}FromRows": 1}
    assert _counts(ctx) == by_method  # no index: an upgrade's view
    with IndexStore.open(cache / INDEX_FILE_NAME) as store:
        store.set_port_source(
            types=[],
            generic_uses=[],
            test_methods=[
                _method("Adds", [f"{STEM}Adds"]),
                _method("Doubles", [f"{STEM}Doubles(2,4)", f"{STEM}Doubles(1,2)"]),
                _method("FromRows", [], exact=False),  # its cases are the report's
            ],
        )
    assert _counts(ctx) == {
        f"{STEM}Adds": 1,
        f"{STEM}Doubles(1,2)": 2,
        f"{STEM}Doubles(2,4)": 2,
        f"{STEM}FromRows": 1,
    }
