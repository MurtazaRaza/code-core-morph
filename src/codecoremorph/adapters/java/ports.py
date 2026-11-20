"""The Java side of a port's index (SPEC §11.12, D-92): what a port from Java records beyond the
symbols, the same whatever the target. The Spoon helper's ``port=1`` rows (types, refusals,
throws, generic uses) are read as the C# index's are (:mod:`codecoremorph.adapters.portindex`);
this module adds what the POMs and the test sources tell: each type's module, each module's
build targets (its main sources a library named by its POM, its test sources a test target
named by their root), and the JUnit test methods with Surefire's IDs.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.java.junit import find_cases
from codecoremorph.adapters.java.surefire import test_id
from codecoremorph.core.models import BuildTarget, TargetKind, TestEntry, TestMethod, TypeEntry
from codecoremorph.patching.textfile import TextFile

# A method whose cases only its run names: a JUnit 5 parameterized, repeated or dynamic test.
_INEXACT_METHOD = re.compile(r"@(?:ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b")
# A JUnit 4 runner whose cases only its run names (Parameterized names them by index or by
# its name pattern, JUnitParams and Theories by their own rules).
_INEXACT_RUNNER = re.compile(
    r"@RunWith\s*\(\s*(?:[\w.]*\.)?(?:Parameterized|JUnitParamsRunner|Theories)\.class\s*\)"
)
_JUNIT5 = re.compile(r"\borg\.junit\.jupiter\.")
_TESTCASE = re.compile(r"\bextends\s+(?:junit\.framework\.)?TestCase\b")


def module_of(poms: Sequence[str], path: str) -> str | None:
    """The POM of the innermost module whose directory holds ``path``."""
    best: str | None = None
    best_depth = -1
    for pom in poms:
        directory = PurePosixPath(pom).parent.as_posix()
        inside = directory == "." or path.startswith(directory.rstrip("/") + "/")
        depth = 0 if directory == "." else len(PurePosixPath(directory).parts)
        if inside and depth > best_depth:
            best, best_depth = pom, depth
    return best


def junit_root(path: str) -> str:
    """The test source root a test file is under: ``core/src/test/java`` for
    ``core/src/test/java/a/BTest.java``, else the file's directory."""
    parts = PurePosixPath(path).parts
    for at in range(len(parts) - 2, 0, -1):
        if parts[at - 1] == "test" and parts[at] in ("java", "kotlin", "groovy"):
            return PurePosixPath(*parts[: at + 1]).as_posix()
        if parts[at] == "test" and at >= 1 and parts[at - 1] == "src":
            return PurePosixPath(*parts[: at + 1]).as_posix()
    return posixpath.dirname(path) or "."


def with_modules(types: Iterable[TypeEntry], poms: Sequence[str]) -> list[TypeEntry]:
    """Each type with its module's POM as its project."""
    return [t.model_copy(update={"project": module_of(poms, t.files[0])}) for t in types]


def build_targets(
    poms: Sequence[str],
    sources: Collection[str],
    tests: Collection[str],
    artifacts: Mapping[str, str] | None = None,
) -> list[BuildTarget]:
    """Each module's main sources, a library target named by its POM, its jar
    (``<artifactId>.jar``, from ``artifacts``: POM -> artifactId) as what it builds, which names
    the port's libraries; and its test sources, a test target named by their root, which
    depends on the library."""
    targets: list[BuildTarget] = []
    for pom in sorted(poms):
        main = sorted(f for f in sources if module_of(poms, f) == pom)
        own_tests = sorted(f for f in tests if module_of(poms, f) == pom)
        if main:
            artifact = (artifacts or {}).get(pom)
            targets.append(
                BuildTarget(
                    name=pom,
                    kind=TargetKind.LIBRARY,
                    files=main,
                    artifacts=[f"{artifact}.jar"] if artifact else [],
                )
            )
        for root in sorted({junit_root(f) for f in own_tests}):
            files = [f for f in own_tests if junit_root(f) == root]
            targets.append(
                BuildTarget(
                    name=root,
                    kind=TargetKind.TEST,
                    files=files,
                    dependencies=[pom] if main else [],
                )
            )
    return targets


def framework(text: str) -> str:
    """The JUnit a test file is written for."""
    if _JUNIT5.search(text):
        return "junit5"
    if _TESTCASE.search(text):
        return "junit3"
    return "junit4"


def junit_methods(worktree: Path, files: Iterable[str]) -> list[TestMethod]:
    """The test methods of ``files`` (worktree paths), each with its case's Surefire ID when the
    source names it exactly; a parameterized method's cases are its run's."""
    found: list[TestMethod] = []
    for path in sorted(files):
        try:
            text = TextFile.decode((worktree / path).read_bytes()).text
        except (OSError, ValueError):
            continue
        kind = framework(text)
        runner = bool(_INEXACT_RUNNER.search(text))
        for case in find_cases(text):
            declaration = text[case.start : case.body_start]
            exact = not runner and not _INEXACT_METHOD.search(declaration)
            found.append(
                TestMethod(
                    file=path,
                    class_name=case.class_name,
                    method=case.method,
                    framework=kind,
                    project=junit_root(path),
                    cases=[test_id(case.class_name, case.method)] if exact else [],
                    exact=exact,
                )
            )
    return found


def junit_entries(methods: Iterable[TestMethod]) -> list[TestEntry]:
    """A test entry per case the sources name exactly, run by its test target."""
    return [
        TestEntry(test_id=case, runs=method.project, file=method.file)
        for method in methods
        if method.exact
        for case in method.cases
    ]
