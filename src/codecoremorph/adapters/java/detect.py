"""What ``codecoremorph init`` reads from a Java project (FR-1.4): Maven, the Java standard the
POMs set, and the layout. It reads the files git tracks and runs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from codecoremorph.adapters.java.pom import (
    POM,
    java_levels,
    lowest_standard,
    modules,
    standard_for_level,
    standard_number,
    uncommented,
)
from codecoremorph.core.models import ProjectDetection
from codecoremorph.patching.textfile import BinaryFileError, TextFile

SUGGESTED_TARGET = "java21"
TEST_DIRS = ["**/src/test"]
_VENDORED = frozenset({"third_party", "thirdparty", "third-party", "vendor", "external"})
_ARTIFACT_ID = re.compile(r"<artifactId>\s*([^<\s]+)\s*</artifactId>")
_BLOCKS = re.compile(
    r"<(parent|dependencies|dependencyManagement|build|profiles|reporting)>.*?</\1>", re.DOTALL
)
_FRAMEWORKS = (
    (re.compile(r"<groupId>\s*org\.junit\.jupiter\s*</groupId>"), "JUnit 5"),
    (re.compile(r"<artifactId>\s*junit\s*</artifactId>"), "JUnit 4 (or 3)"),
    (re.compile(r"<groupId>\s*org\.testng\s*</groupId>"), "TestNG"),
)


def _text(root: Path, rel: str) -> str | None:
    try:
        return TextFile.decode((root / rel).read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def _vendored(path: str) -> bool:
    return any(part in _VENDORED for part in PurePosixPath(path).parts[:-1])


def project_name(pom: str) -> str | None:
    """The POM's own ``artifactId`` (not its parent's or a dependency's)."""
    own = _BLOCKS.sub("", uncommented(pom))
    match = _ARTIFACT_ID.search(own)
    return match.group(1) if match else None


def detect(root: Path, files: list[str]) -> ProjectDetection | None:
    code = [f for f in files if f.endswith(".java") and not _vendored(f)]
    if not code:
        return None
    notes: list[str] = []
    top = _text(root, POM) if POM in files else None
    if top is None:
        gradle = any(PurePosixPath(f).name.startswith("build.gradle") for f in files)
        notes.append(
            "No top-level pom.xml: CodeCoreMorph builds Java with Maven"
            + (" (Gradle comes later)." if gradle else ".")
        )
    poms = [f for f in files if PurePosixPath(f).name == POM and not _vendored(f)]
    texts = [t for p in poms if (t := _text(root, p)) is not None]
    standard = lowest_standard(texts)
    guessed = standard is None
    if standard is None:
        standard = "java8"
        note = "no POM sets the Java level, and Maven's compiler plugin defaults to 1.8 or older"
    else:
        levels = sorted({lv for t in texts for lv in java_levels(t)})
        note = "the POMs set " + ", ".join(levels)
        odd = [
            lv
            for lv in levels
            if (s := standard_for_level(lv)) and _level_number(lv) != standard_number(s)
        ]
        if odd:
            notes.append(
                f"The POMs set Java {', '.join(odd)}, which builds on the next LTS release's JDK."
            )
    if top is not None and modules(top):
        notes.append(f"A multi-module build: {len(modules(top))} module(s) in the top-level POM.")
    frameworks = [name for pattern, name in _FRAMEWORKS if any(pattern.search(t) for t in texts)]
    if frameworks:
        notes.append("Tests: " + ", ".join(frameworks) + ", run by Surefire.")
    else:
        notes.append("No test framework found in the POMs: the run's parity rests on its tests.")
    target = SUGGESTED_TARGET if standard_number(standard) < 21 else "java25"
    return ProjectDetection(
        language="java",
        build_system="maven" if top is not None else None,
        name=project_name(top) if top is not None else None,
        standard=standard,
        standard_note=note,
        standard_guessed=guessed,
        target_standard=target,
        source_dirs=["."],
        test_dirs=list(TEST_DIRS),
        exclude=sorted({f"{p}/**" for f in files if _vendored(f) for p in _vendored_root(f)}),
        notes=notes,
        code_files=len(code),
    )


def _level_number(level: str) -> int:
    text = level.strip().removeprefix("1.")
    return int(text) if text.isdigit() else 0


def _vendored_root(path: str) -> list[str]:
    parts = PurePosixPath(path).parts
    for i, part in enumerate(parts[:-1]):
        if part in _VENDORED:
            return ["/".join(parts[: i + 1])]
    return []
