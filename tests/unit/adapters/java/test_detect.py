"""What ``codecoremorph init`` reads from a Java project (FR-1.4), and the records query."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.java.adapter import record_candidates
from codecoremorph.adapters.java.detect import detect, project_name
from codecoremorph.store.index import IndexStore

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "java"


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_the_ledger_fixture() -> None:
    root = FIXTURES / "legacy-ledger"
    found = detect(root, _files(root))
    assert found is not None
    assert (found.language, found.build_system, found.name) == ("java", "maven", "legacy-ledger")
    assert (found.standard, found.standard_guessed, found.target_standard) == (
        "java8",
        False,
        "java21",
    )
    assert found.test_dirs == ["**/src/test"]
    assert any("JUnit 4" in n for n in found.notes)
    assert found.code_files == 10


def test_the_multi_module_fixture() -> None:
    root = FIXTURES / "task-planner"
    found = detect(root, _files(root))
    assert found is not None
    assert found.standard == "java8"  # through ${java.version}
    assert any("2 module(s)" in n for n in found.notes)
    assert any("JUnit 5" in n for n in found.notes)


def test_no_java_no_pom_and_guessed_levels(tmp_path: Path) -> None:
    assert detect(tmp_path, ["src/main.cpp"]) is None
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    found = detect(tmp_path, ["build.gradle", "src/A.java"])
    assert found is not None
    assert found.build_system is None
    assert found.standard_guessed
    assert "Gradle comes later" in found.notes[0]


def test_the_projects_own_artifact_id() -> None:
    pom = (
        "<project><parent><artifactId>parent</artifactId></parent>"
        "<artifactId>mine</artifactId><dependencies><dependency><artifactId>dep</artifactId>"
        "</dependency></dependencies></project>"
    )
    assert project_name(pom) == "mine"


def _sym(sid: str, kind: str, signature: str, file: str = "M.java") -> tuple[object, ...]:
    name = sid.rsplit("#", 1)[-1] if "#" in sid else sid.rsplit(".", 1)[-1]
    return (
        sid,
        name,
        sid.removeprefix("java:"),
        kind,
        file,
        3,
        1,
        9,
        1,
        signature,
        "public",
        1,
        "none",
    )


def test_record_candidates_are_value_classes(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite"
    rows = [
        _sym("java:a.Money", "class", "public final class Money"),
        _sym("java:a.Money#cents", "field", "private final long cents"),
        _sym("java:a.Money#ZERO", "field", "public static final a.Money ZERO"),
        _sym(
            "java:a.Money#equals(java.lang.Object)",
            "method",
            "public boolean equals(java.lang.Object)",
        ),
        _sym("java:a.Money#hashCode()", "method", "public int hashCode()"),
        _sym("java:a.Mutable", "class", "public class Mutable"),
        _sym("java:a.Mutable#n", "field", "private int n"),
        _sym(
            "java:a.Mutable#equals(java.lang.Object)",
            "method",
            "public boolean equals(java.lang.Object)",
        ),
        _sym("java:a.Mutable#hashCode()", "method", "public int hashCode()"),
        _sym("java:a.Base", "class", "public class Base"),
        _sym("java:a.Base#n", "field", "private final int n"),
        _sym(
            "java:a.Base#equals(java.lang.Object)",
            "method",
            "public boolean equals(java.lang.Object)",
        ),
        _sym("java:a.Base#hashCode()", "method", "public int hashCode()"),
    ]
    with IndexStore.open(path, reset=True) as store:
        store.write(
            files=[("M.java", "java", "0" * 64, "source", 0, 0, "utf-8", "lf")],
            symbols=rows,  # type: ignore[arg-type]
            references=[("M.java", "java:a.Sub", "java:a.Base", "inherit", 20, 1)],
        )
    sites = record_candidates(path, ["M.java"])
    assert [s.text for s in sites] == ["a.Money: 1 final field(s), equals and hashCode"]
    assert record_candidates(path, ["Other.java"]) == []


def test_the_files_maven_compiles(tmp_path: Path) -> None:
    from codecoremorph.adapters.java.indexing import project_layout
    from codecoremorph.core.globs import PathMatcher

    def write(rel: str, text: str = "class X {}") -> None:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    write("pom.xml", "<project><modules><module>core</module></modules></project>")
    helper = (
        "<project><build><plugins><plugin><artifactId>build-helper-maven-plugin</artifactId>"
        "<configuration><sources><source>${project.basedir}/../extra</source></sources>"
        "</configuration></plugin></plugins></build></project>"
    )
    write("core/pom.xml", helper)
    write("core/src/main/java/a/A.java")
    write("core/src/test/java/a/ATest.java")
    write("core/target/generated-sources/a/G.java")
    write("extra/a/Extra.java")
    write("src/it/sample/B.java")
    write("vendor/c/C.java")
    poms, compiled, uncompiled = project_layout(tmp_path, PathMatcher(["vendor/**"]))
    assert poms == ["pom.xml", "core/pom.xml"]
    assert compiled == [
        "core/src/main/java/a/A.java",
        "core/src/test/java/a/ATest.java",
        "extra/a/Extra.java",
    ]
    assert uncompiled == ["src/it/sample/B.java"]
