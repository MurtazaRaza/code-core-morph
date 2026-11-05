"""Maven POMs as text: the Java standards they set, raising them (``maven-java-version``), the
modules they list, and the build extensions they load (SPEC §11.2, D-63).

Edits are made on the text, never by rewriting the XML, so a POM keeps its layout, comments
and line endings. Comments are blanked out before anything is read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STANDARDS = ("java8", "java11", "java17", "java21", "java25")
POM = "pom.xml"

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Properties and compiler-plugin settings that name the Java level of a build.
_LEVEL_PROPERTIES = (
    "maven.compiler.source",
    "maven.compiler.target",
    "maven.compiler.release",
    "maven.compiler.testSource",
    "maven.compiler.testTarget",
    "maven.compiler.testRelease",
    "java.version",  # Spring Boot's parent, and many projects of their own
)
_PLUGIN_SETTINGS = ("source", "target", "release", "testSource", "testTarget", "testRelease")
_PLUGIN_BLOCK = re.compile(r"<plugin>(?:(?!</plugin>).)*</plugin>", re.DOTALL)
_COMPILER_PLUGIN = re.compile(r"<artifactId>\s*maven-compiler-plugin\s*</artifactId>")
_PROPERTIES_BLOCK = re.compile(r"<properties>(?:(?!</properties>).)*</properties>", re.DOTALL)
_MODULE = re.compile(r"<module>\s*([^<\s]+)\s*</module>")
_EXTENSION = re.compile(
    r"<extensions>\s*true\s*</extensions>|<extension>(?:(?!</extension>).)*</extension>",
    re.DOTALL,
)
_PROPERTY_REF = re.compile(r"^\$\{([\w.-]+)\}$")


def standard_number(standard: str) -> int:
    """``java21`` → 21."""
    return int(standard.removeprefix("java"))


def standard_for_level(level: str) -> str | None:
    """The standard a Java level builds as: ``1.8`` and ``8`` are ``java8``, a level between
    two LTS releases rounds up (``10`` is ``java11``), and older code builds as ``java8``,
    whose JDK compiles it. None for a level no JDK of the toolchain has, or no number."""
    text = level.strip()
    if text.startswith("1."):
        text = text[2:]
    if not text.isdigit():
        return None
    number = int(text)
    for standard in STANDARDS:
        if standard_number(standard) >= number:
            return standard
    return None


def level_text(standard: str) -> str:
    """How a POM writes a standard's level: ``1.8`` for Java 8 (what Java 8 projects use),
    the bare number from 9 on."""
    number = standard_number(standard)
    return "1.8" if number == 8 else str(number)


def uncommented(text: str) -> str:
    """``text`` with XML comments blanked out (same length, line breaks kept)."""
    return _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _tag(name: str) -> re.Pattern[str]:
    return re.compile(rf"(<{re.escape(name)}>)\s*([^<]*?)\s*(</{re.escape(name)}>)")


def properties(text: str) -> dict[str, str]:
    """The POM's own ``<properties>`` (a parent's are not read)."""
    found: dict[str, str] = {}
    for block in _PROPERTIES_BLOCK.finditer(uncommented(text)):
        for match in re.finditer(r"<([\w.-]+)>\s*([^<]*?)\s*</\1>", block.group(0)):
            found.setdefault(match.group(1), match.group(2))
    return found


def _resolve(value: str, props: dict[str, str]) -> str:
    seen: set[str] = set()
    while (match := _PROPERTY_REF.match(value.strip())) is not None:
        name = match.group(1)
        if name in seen or name not in props:
            return value
        seen.add(name)
        value = props[name]
    return value


def java_levels(text: str) -> list[str]:
    """The Java levels the POM sets, resolved through its own properties: compiler
    properties, ``java.version``, and the compiler plugin's ``source``, ``target`` and
    ``release``."""
    clean = uncommented(text)
    props = properties(text)
    levels: list[str] = []
    for name in _LEVEL_PROPERTIES:
        if name in props:
            levels.append(_resolve(props[name], props))
    for block in _PLUGIN_BLOCK.finditer(clean):
        if not _COMPILER_PLUGIN.search(block.group(0)):
            continue
        for name in _PLUGIN_SETTINGS:
            for match in _tag(name).finditer(block.group(0)):
                levels.append(_resolve(match.group(2), props))
    return [level for level in levels if level]


def lowest_standard(texts: list[str]) -> str | None:
    """The oldest standard any of the POMs sets, or None when none sets one."""
    found = [s for text in texts for level in java_levels(text) if (s := standard_for_level(level))]
    return min(found, key=standard_number) if found else None


@dataclass(frozen=True)
class LevelEdit:
    text: str
    replacements: int


def bump_java_level(text: str, standard: str, *, top_level: bool) -> LevelEdit:
    """``text`` with every Java level it sets raised to ``standard``'s (none is lowered); a
    level that names a property is left, since the property is raised where it is set. A
    top-level POM that sets no level gets ``maven.compiler.source`` and ``target``, in its
    ``<properties>`` (made if it has none), since Maven's default level is older than any
    target."""
    target = standard_number(standard)
    new_level = level_text(standard)
    clean = uncommented(text)
    spans: list[tuple[int, int]] = []

    def collect(pattern: re.Pattern[str], start: int, end: int) -> None:
        for match in pattern.finditer(clean, start, end):
            value = match.group(2)
            current = standard_for_level(value)
            if _PROPERTY_REF.match(value.strip()) or not value.strip():
                continue
            if current is not None and standard_number(current) >= target:
                continue
            spans.append((match.start(2), match.end(2)))

    for block in _PROPERTIES_BLOCK.finditer(clean):
        for name in _LEVEL_PROPERTIES:
            collect(_tag(name), block.start(), block.end())
    for block in _PLUGIN_BLOCK.finditer(clean):
        if _COMPILER_PLUGIN.search(block.group(0)):
            for name in _PLUGIN_SETTINGS:
                collect(_tag(name), block.start(), block.end())
    if spans:
        out: list[str] = []
        position = 0
        for start, end in sorted(set(spans)):
            out.append(text[position:start])
            out.append(new_level)
            position = end
        out.append(text[position:])
        return LevelEdit("".join(out), len(set(spans)))
    if not top_level or java_levels(text):
        return LevelEdit(text, 0)
    return _add_levels(text, clean, new_level)


def _add_levels(text: str, clean: str, level: str) -> LevelEdit:
    newline = "\r\n" if "\r\n" in text else "\n"
    block = _PROPERTIES_BLOCK.search(clean)
    if block is not None:
        closing = block.end() - len("</properties>")
        indent = _indent_before(text, closing)
        inner = indent + "  "
        lines = "".join(
            f"{inner}<{name}>{level}</{name}>{newline}"
            for name in ("maven.compiler.source", "maven.compiler.target")
        )
        line_start = text.rfind("\n", 0, closing) + 1
        return LevelEdit(text[:line_start] + lines + text[line_start:], 2)
    project_end = clean.rfind("</project>")
    if project_end < 0:
        return LevelEdit(text, 0)
    indent = "  "
    lines = (
        f"{indent}<properties>{newline}"
        f"{indent}{indent}<maven.compiler.source>{level}</maven.compiler.source>{newline}"
        f"{indent}{indent}<maven.compiler.target>{level}</maven.compiler.target>{newline}"
        f"{indent}</properties>{newline}"
    )
    line_start = text.rfind("\n", 0, project_end) + 1
    return LevelEdit(text[:line_start] + lines + text[line_start:], 2)


def _indent_before(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    prefix = text[line_start:offset]
    return prefix if not prefix.strip() else ""


_SOURCE_DIRECTORY = re.compile(
    r"<(?:sourceDirectory|testSourceDirectory)>\s*([^<]+?)\s*</(?:sourceDirectory|testSourceDirectory)>"
)
_BUILD_HELPER = re.compile(r"<artifactId>\s*build-helper-maven-plugin\s*</artifactId>")
_HELPER_SOURCE = re.compile(r"<source>\s*([^<]+?)\s*</source>")
DEFAULT_SOURCE_ROOTS = ("src/main/java", "src/test/java")


def source_roots(text: str) -> list[str]:
    """The directories, relative to the POM, that its module compiles: Maven's defaults, the
    ``sourceDirectory`` and ``testSourceDirectory`` it sets, and the roots it adds with the
    build-helper plugin (characterization tests, D-63)."""
    clean = uncommented(text)
    found: list[str] = list(DEFAULT_SOURCE_ROOTS)
    values = _SOURCE_DIRECTORY.findall(clean)
    for block in _PLUGIN_BLOCK.finditer(clean):
        if _BUILD_HELPER.search(block.group(0)):
            values += _HELPER_SOURCE.findall(block.group(0))
    for value in values:
        root = re.sub(r"^\$\{(?:project\.)?basedir\}/?", "", value.replace("\\", "/")).strip("/")
        if root and "${" not in root and root not in found:
            found.append(root)
    return found


def modules(text: str) -> list[str]:
    """The ``<module>`` directories the POM lists, relative to it."""
    return _MODULE.findall(uncommented(text))


def extensions(text: str) -> list[str]:
    """The build extensions a POM loads (``<extensions>true</extensions>`` plugins and
    ``<extension>`` entries), whitespace-normalized: Maven runs their code while it reads the
    POM (D-63)."""
    return [" ".join(m.group(0).split()) for m in _EXTENSION.finditer(uncommented(text))]
