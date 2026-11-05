"""Maven's output as diagnostics, against captured builds (SPEC §11.2)."""

from __future__ import annotations

from pathlib import Path

from codecoremorph.adapters.java.diagnostics import (
    PathMap,
    build_failure,
    maven_errors,
    parse_maven_output,
)
from codecoremorph.core.models import DiagnosticOrigin, Severity

DATA = Path(__file__).resolve().parents[3] / "data" / "java" / "maven"


def _read(name: str) -> str:
    return (DATA / name).read_bytes().decode("utf-8")


def test_javac_errors_on_windows_once_each_with_their_details() -> None:
    """A multi-module build on Windows: javac prints /E:/... paths, and Maven repeats the
    errors in its failure summary with [ERROR] before the detail lines."""
    mirror = "E:\\codecoremorph-home\\cache\\20261105-153117-31bb\\maven\\project"
    paths = PathMap([mirror], case_insensitive=True)
    found = parse_maven_output(_read("compile_errors_windows.log"), paths)
    errors = [d for d in found if d.severity is Severity.ERROR]
    assert [(d.file_path, d.line, d.column) for d in errors] == [
        ("app/src/main/java/org/example/app/Planner.java", 10, 24),
        ("app/src/main/java/org/example/app/Planner.java", 22, 6),
    ]
    assert errors[0].message == "package javax.annotation does not exist"
    assert errors[1].message == (
        "cannot find symbol\nsymbol:   class PostConstruct\nlocation: class org.example.app.Planner"
    )
    assert all(d.origin is DiagnosticOrigin.COMPILER for d in found)


def test_javac_errors_in_docker() -> None:
    paths = PathMap(["/cache/maven/project"], case_insensitive=False)
    found = parse_maven_output(_read("compile_errors_docker.log"), paths)
    errors = [d for d in found if d.severity is Severity.ERROR]
    assert [(d.file_path, d.line) for d in errors] == [("src/main/java/org/example/Codec.java", 23)]
    assert "symbol:   variable Base64" in errors[0].message


def test_warnings_keep_their_file() -> None:
    output = (
        "[WARNING] /work/m/src/main/java/a/B.java:[52,40] Integer(int) in java.lang.Integer has "
        "been deprecated and marked for removal\n"
        "[WARNING] source value 8 is obsolete and will be removed in a future release\n"
    )
    found = parse_maven_output(output, PathMap(["/work/m"], case_insensitive=False))
    assert [(d.file_path, d.line, d.severity) for d in found] == [
        ("src/main/java/a/B.java", 52, Severity.WARNING)
    ]


def test_a_path_outside_the_mirror_stays_absolute() -> None:
    output = "[ERROR] /elsewhere/Gen.java:[1,1] class X is public\n"
    found = parse_maven_output(output, PathMap(["/work/m"], case_insensitive=False))
    assert found[0].file_path == "/elsewhere/Gen.java"


def test_maven_errors_without_their_boilerplate() -> None:
    """Resolving a sibling module that wasn't built: the failure the OpenRewrite run hit
    before it compiled first (D-64)."""
    errors = maven_errors(_read("reactor_resolution_failure.log"))
    assert errors[0].startswith("Failed to execute goal on project task-planner-app")
    assert "Could not find artifact org.example:task-planner-core:jar:1.0-SNAPSHOT" in errors
    assert not any("Help 1" in e or "re-run Maven" in e.lower() for e in errors)
    failure = build_failure(_read("reactor_resolution_failure.log"), "codemod", "x.log")
    assert failure.origin is DiagnosticOrigin.BUILD_SYSTEM
    assert failure.message.startswith("Maven's codemod failed:\nFailed to execute goal")


def test_a_pom_problem_names_its_line() -> None:
    output = (
        "[ERROR] [ERROR] Some problems were encountered while processing the POMs:\n"
        "[ERROR] 'dependencies.dependency.version' for junit:junit:jar is missing. "
        "@ line 18, column 17\n"
    )
    failure = build_failure(output, "build", "x.log")
    assert (failure.file_path, failure.line) == ("pom.xml", 18)
    assert build_failure("", "build", "x.log").message == "Maven's build failed; see x.log"
