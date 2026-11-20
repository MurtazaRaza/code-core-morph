"""The Java side of a port from Java, the same for every target (SPEC §11.12, D-91, D-92): the
names of a port's libraries and units, the files a façade module gets in its sources
(``codecoremorph/interop``: the support ``CcmInterop.java``, the project's exception factory
``CcmExceptions.java`` and the generated ``CcmNative.java``), the façade modules, and what the
retirement deletes of the Java.

The support and the bridge's C ABI (``codecoremorph_bridge.h``) ship in ``port_runtime``; a port
to C++ (``adapters/cpp/javaports.py``) and one to Rust write the same Java, so the façades don't
depend on the target.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Mapping, Sequence
from importlib import resources
from pathlib import Path, PurePosixPath

from codecoremorph.adapters import leftovers
from codecoremorph.adapters.csharp.ports import Writer, snake
from codecoremorph.adapters.csharp.ports import unit_name as _type_unit_name
from codecoremorph.adapters.java import jni
from codecoremorph.adapters.java.pom import uncommented
from codecoremorph.core.errors import ConfigError

INTEROP_DIR = "codecoremorph/interop"
SUPPORT_FILE = "CcmInterop.java"
EXCEPTIONS_FILE = "CcmExceptions.java"
GENERATED_FILE = "CcmNative.java"
BRIDGE_RUNTIME_HEADER = "codecoremorph_bridge.h"
FACADE_FILES = (SUPPORT_FILE, EXCEPTIONS_FILE, GENERATED_FILE)
# What Maven reads beside the POMs, which the retirement deletes with them.
MAVEN_FILES = ("mvnw", "mvnw.cmd")
MAVEN_DIR = ".mvn"
RUNS_MAVEN = re.compile(r"(?<![\w-])(?:mvn|mvnw|maven|gradle|gradlew)(?![\w-])")
_SOURCE_DIRECTORY = re.compile(r"<sourceDirectory>\s*([^<]+?)\s*</sourceDirectory>")


def library_name(naming: str) -> str:
    """The stem of the target's libraries for a Java module, from its jar (``order-book.jar``
    gives ``order_book``) or, failing that, its POM's directory."""
    stem = PurePosixPath(naming.replace("\\", "/")).name
    if stem.lower().endswith(".jar"):
        stem = stem[: -len(".jar")]
    elif stem == "pom.xml":
        stem = PurePosixPath(naming.replace("\\", "/")).parent.name or "java"
    return snake(stem)


def unit_name(type_name: str, taken: Collection[str], reserved: Collection[str] = ()) -> str:
    """The stem of a type unit's files, from its first type's name: ``shop.Cart`` gives
    ``cart``, ``shop.Cart.ItemFilter`` ``item_filter``."""
    return _type_unit_name(type_name.replace("$", "."), taken, reserved)


def type_names(types: str) -> str:
    """A unit's ``types`` option (symbol IDs, comma-separated) as the stubs' comments name
    them: ``java:shop.Cart,java:shop.Cart$ItemFilter`` gives ``shop.Cart, shop.Cart.ItemFilter``."""
    names = [t.strip().removeprefix("java:").replace("$", ".") for t in types.split(",")]
    return ", ".join(n for n in names if n)


def runtime_text(file_name: str) -> str:
    package = resources.files("codecoremorph.adapters.java.port_runtime")
    return (package / file_name).read_text(encoding="utf-8")


def module_option(worktree: Path, options: Mapping[str, str], port: str) -> str:
    """The façade module's POM (the scaffold's ``build_target`` option)."""
    pom = options.get("build_target", "").replace("\\", "/")
    if PurePosixPath(pom).name != "pom.xml" or not (worktree / pom).is_file():
        raise ConfigError(
            f"the {port} scaffold needs the façade module's POM, got {pom!r}",
            hint="The planner sets it from the unit's types; re-plan the run.",
        )
    return pom


def main_source_root(pom: str, pom_text: str) -> str:
    """The worktree directory of a module's main sources: its ``sourceDirectory`` or Maven's
    ``src/main/java``."""
    match = _SOURCE_DIRECTORY.search(uncommented(pom_text))
    root = "src/main/java"
    if match is not None and "${" not in match.group(1):
        root = re.sub(r"^\$\{(?:project\.)?basedir\}/?", "", match.group(1)).strip("/") or root
    base = PurePosixPath(pom).parent.as_posix()
    return root if base == "." else f"{base}/{root}"


def interop_dir(pom: str, pom_text: str) -> str:
    """Where a façade module's support goes: ``<sources>/codecoremorph/interop``."""
    return f"{main_source_root(pom, pom_text)}/{INTEROP_DIR}"


def write_facade_support(out: Writer, pom: str, library: str) -> str:
    """The support, the exception factory and the initial declarations in a façade module, once;
    the interop directory."""
    directory = interop_dir(pom, out.read(pom) or "")
    out.write(f"{directory}/{SUPPORT_FILE}", runtime_text(SUPPORT_FILE))
    out.create(f"{directory}/{EXCEPTIONS_FILE}", runtime_text(EXCEPTIONS_FILE))
    out.create(f"{directory}/{GENERATED_FILE}", jni.initial_declarations(library))
    return directory


def facade_dirs(worktree: Path) -> list[str]:
    """The interop directories (worktree paths) of the modules that hold façades: those with
    the support file."""
    found: list[str] = []
    for support in worktree.rglob(f"{INTEROP_DIR}/{SUPPORT_FILE}"):
        rel = support.relative_to(worktree)
        if any(part in {"target", "build", ".git"} for part in rel.parts[:-1]):
            continue
        found.append(rel.parent.as_posix())
    return sorted(found)


def retire_java(out: Writer, files: Sequence[str]) -> None:
    """The Java half of a retirement: the unit's Java files and POMs, Maven's wrapper and
    settings, and the façades' support."""
    for rel in files:
        if rel.endswith(".java") or posixpath.basename(rel) == "pom.xml":
            out.delete(rel)
    for name in MAVEN_FILES:
        out.delete(name)
    out.delete_tree(MAVEN_DIR)
    for directory in facade_dirs(out.worktree):
        for file_name in FACADE_FILES:
            out.delete(f"{directory}/{file_name}")


def retirement_left(worktree: Path, build: str) -> list[tuple[str, str]]:
    """What still builds the Java the retirement deleted, for review (D-89): the CI
    configurations that run Maven or Gradle, which the project rewrites for ``build``.
    (file, reason) pairs."""
    left: list[tuple[str, str]] = []
    for rel, path in leftovers.worktree_files(worktree):
        if not leftovers.is_ci_file(rel):
            continue
        text = leftovers.read_text(path)
        if text is not None and RUNS_MAVEN.search(text):
            left.append((rel, f"a CI configuration that builds the Java: rewrite it for {build}"))
    return left
