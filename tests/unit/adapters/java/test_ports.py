"""The Java side of a port's index (SPEC §11.12, D-92): modules, build targets and JUnit test
methods with Surefire's IDs."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.java.ports import (
    build_targets,
    framework,
    junit_methods,
    junit_root,
    module_of,
)
from codecoremorph.core.models import TargetKind

POMS = ["pom.xml", "core/pom.xml", "app/pom.xml"]


def test_a_file_belongs_to_its_innermost_module() -> None:
    assert module_of(POMS, "core/src/main/java/a/B.java") == "core/pom.xml"
    assert module_of(POMS, "src/main/java/a/B.java") == "pom.xml"
    assert module_of(["core/pom.xml"], "app/src/A.java") is None
    assert junit_root("core/src/test/java/a/BTest.java") == "core/src/test/java"
    assert junit_root("src/test/java/BTest.java") == "src/test/java"


def test_each_module_is_a_library_and_its_tests_a_test_target() -> None:
    targets = build_targets(
        POMS,
        ["core/src/main/java/a/B.java", "app/src/main/java/a/App.java"],
        ["core/src/test/java/a/BTest.java"],
    )
    by_name = {t.name: t for t in targets}
    assert by_name["core/pom.xml"].kind is TargetKind.LIBRARY
    assert by_name["core/pom.xml"].files == ["core/src/main/java/a/B.java"]
    tests = by_name["core/src/test/java"]
    assert tests.kind is TargetKind.TEST
    assert tests.dependencies == ["core/pom.xml"]
    assert "pom.xml" not in by_name  # the parent POM compiles nothing


def test_the_junit_of_a_file() -> None:
    assert framework("import org.junit.jupiter.api.Test;") == "junit5"
    assert framework("public class ATest extends TestCase {") == "junit3"
    assert framework("import org.junit.Test;") == "junit4"


def test_parameterized_methods_take_their_cases_from_the_run(tmp_path: Path) -> None:
    junit4 = tmp_path / "src/test/java/a/ParseTest.java"
    junit4.parent.mkdir(parents=True)
    junit4.write_text(
        "package a;\nimport org.junit.Test;\nimport org.junit.runner.RunWith;\n"
        "import org.junit.runners.Parameterized;\n"
        "@RunWith(Parameterized.class)\npublic class ParseTest {\n"
        "    @Test\n    public void parses() {\n    }\n}\n",
        encoding="utf-8",
    )
    junit5 = tmp_path / "src/test/java/a/MathTest.java"
    junit5.write_text(
        "package a;\nimport org.junit.jupiter.api.Test;\n"
        "import org.junit.jupiter.params.ParameterizedTest;\n"
        "class MathTest {\n    @Test\n    void adds() {\n    }\n\n"
        "    @ParameterizedTest\n    @ValueSource(ints = {1, 2})\n"
        "    void signs(int n) {\n    }\n}\n",
        encoding="utf-8",
    )
    methods = junit_methods(
        tmp_path, ["src/test/java/a/ParseTest.java", "src/test/java/a/MathTest.java"]
    )
    by_method = {m.method: m for m in methods}
    assert by_method["parses"].exact is False
    assert by_method["parses"].framework == "junit4"
    assert by_method["adds"].cases == ["junit:a.MathTest#adds"]
    assert by_method["adds"].project == "src/test/java"
    assert by_method["signs"].exact is False
    assert by_method["signs"].cases == []
