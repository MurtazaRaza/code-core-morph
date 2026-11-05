"""Host side of Java indexing (SPEC §7.2, §11.2, D-63): the Spoon helper runs in the sandbox on
the files Maven compiles, and its JSON lines become the SQLite index. The POMs are build files.

The helper is compiled from its source into the adapter's cache with the toolchain's newest
``javac``, once per helper version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import time
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, cast

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.java.maven import JavaToolchain
from codecoremorph.adapters.java.pom import POM, source_roots, standard_number
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import FileRole, IndexMeta, LineEnding, SymbolIndex
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.sandbox.executor import full_output
from codecoremorph.store.index import (
    INDEX_FILE_NAME,
    DependencyRow,
    FileRow,
    IndexStore,
    ReferenceRow,
    SymbolRow,
)

log = logging.getLogger(__name__)

HELPER_SOURCE = "JavaIndexHelper.java"
HELPER_CLASS = "JavaIndexHelper"
_SKIPPED_DIRS = frozenset({".git", ".codecoremorph", ".mvn", "node_modules"})
_PUBLIC = ("public", "protected")


def line_ending(data: bytes) -> LineEnding:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return LineEnding.MIXED
    if crlf:
        return LineEnding.CRLF
    return LineEnding.LF if lf else LineEnding.NONE


def project_layout(worktree: Path, exclude: PathMatcher) -> tuple[list[str], list[str], list[str]]:
    """``(poms, compiled, uncompiled)``: the POMs, the Java files under a module's source
    roots, and the Java files outside every root (another build's code, sample projects),
    which no build checks. Excluded paths and Maven outputs are left out."""
    poms: list[str] = []
    java: list[str] = []
    for current, dirs, files in os.walk(worktree):
        here = Path(current)
        rel_dir = here.relative_to(worktree).as_posix()
        is_module = POM in files
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIPPED_DIRS
            and not (is_module and d == "target")
            and not exclude.matches(d if rel_dir == "." else f"{rel_dir}/{d}")
        )
        for name in sorted(files):
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if exclude.matches(rel):
                continue
            if name == POM:
                poms.append(rel)
            elif name.endswith(".java"):
                java.append(rel)
    roots: list[str] = []
    for pom in poms:
        base = PurePosixPath(pom).parent
        text = _text(worktree / pom) or ""
        for root in source_roots(text):
            joined = posixpath.normpath((base / root).as_posix())  # core/../tests: tests
            if not joined.startswith("../"):
                roots.append("" if joined == "." else joined)
    compiled = [f for f in java if any(_under(f, r) for r in roots)]
    seen = set(compiled)
    uncompiled = [f for f in java if f not in seen]
    return poms, compiled, uncompiled


def _under(path: str, root: str) -> bool:
    return not root or path.startswith(root.rstrip("/") + "/")


def _text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def helper_classes(ctx: AdapterContext, tools: JavaToolchain) -> Path:
    """The compiled helper in the cache, compiled again when its source changed."""
    source = resources.files("codecoremorph.adapters.java.helpers").joinpath(HELPER_SOURCE)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    base = ctx.cache_dir / "helpers" / "java"
    classes = base / "classes"
    stamp = classes / ".source-sha256"
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == digest:
        return classes
    base.mkdir(parents=True, exist_ok=True)
    (base / HELPER_SOURCE).write_bytes(data)
    classes.mkdir(parents=True, exist_ok=True)
    jdk = tools.newest_jdk()
    argv = [
        tools.javac(jdk),
        "-encoding",
        "UTF-8",
        "-proc:none",
        "-nowarn",
        "-d",
        ctx.sandbox(classes),
        "-cp",
        tools.spoon_jar,
        ctx.sandbox(base / HELPER_SOURCE),
    ]
    result = ctx.executor.run(
        argv,
        cwd=ctx.sandbox(base),
        env={"JAVA_HOME": jdk},
        timeout_s=300,
        log_path=ctx.log_dir / "index-helper-javac.log",
    )
    if not result.ok:
        stdout, stderr = full_output(result)
        raise SandboxError(
            "couldn't compile the Java index helper",
            reason="\n".join((stdout + stderr).strip().splitlines()[-10:]) or None,
        )
    stamp.write_text(digest, encoding="utf-8")
    return classes


def file_row(worktree: Path, path: str, *, role: FileRole, language: str, public: bool) -> FileRow:
    data = (worktree / path).read_bytes()
    try:
        encoding = TextFile.decode(data).encoding
    except BinaryFileError:
        encoding = "binary"
    return (
        path,
        language,
        hashlib.sha256(data).hexdigest(),
        role.value,
        0,
        int(public),
        encoding,
        line_ending(data).value,
    )


def run_index(ctx: AdapterContext, tools: JavaToolchain, *, adapter_version: str) -> SymbolIndex:
    started = time.monotonic()
    ctx.cache_dir.mkdir(parents=True, exist_ok=True)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    exclude = PathMatcher(ctx.lang.exclude)
    tests = PathMatcher(ctx.lang.test_dirs)
    public_api = PathMatcher(ctx.lang.public_api)
    poms, compiled, uncompiled = project_layout(ctx.worktree, exclude)
    work = ctx.cache_dir / "index-run"
    work.mkdir(parents=True, exist_ok=True)
    output = work / "index.jsonl"
    output.unlink(missing_ok=True)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    if compiled:
        classes = helper_classes(ctx, tools)
        request = work / "request.txt"
        lines = [
            f"root={ctx.sandbox(ctx.worktree)}",
            f"output={ctx.sandbox(output)}",
            f"compliance={standard_number(ctx.lang.standard)}",
            *[f"source={ctx.sandbox(ctx.worktree / f)}" for f in compiled],
        ]
        request.write_text("\n".join(lines) + "\n", encoding="utf-8")
        jdk = tools.newest_jdk()
        classpath = tools.classpath_separator.join([tools.spoon_jar, ctx.sandbox(classes)])
        result = ctx.executor.run(
            [tools.java(jdk), "-cp", classpath, HELPER_CLASS, ctx.sandbox(request)],
            cwd=ctx.sandbox(work),
            env={"JAVA_HOME": jdk},
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "index-helper.log",
        )
        if not result.ok or not output.is_file():
            stdout, stderr = full_output(result)
            raise SandboxError(
                "the Java index helper failed",
                reason="\n".join((stdout + stderr).strip().splitlines()[-15:]) or None,
                hint=f"See {result.log_path}.",
            )
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(cast(dict[str, Any], json.loads(line)))
    files: list[FileRow] = []
    for pom in poms:
        files.append(
            file_row(ctx.worktree, pom, role=FileRole.BUILD, language="maven", public=False)
        )
    for path in compiled:
        role = FileRole.TEST if tests.matches(path) else FileRole.SOURCE
        files.append(
            file_row(
                ctx.worktree, path, role=role, language="java", public=public_api.matches(path)
            )
        )
    known = {f[0] for f in files}
    symbols: list[SymbolRow] = []
    references: list[ReferenceRow] = []
    dependencies: list[DependencyRow] = []
    for row in rows:
        kind = row.get("t")
        if kind == "problem":
            problems.append(str(row.get("message")))
        elif kind == "sym" and row["file"] in known:
            boundary = str(row["b"])
            if boundary == "none" and row["vis"] in _PUBLIC and public_api.matches(row["file"]):
                boundary = "public_api"
            symbols.append(
                (
                    str(row["id"]),
                    str(row["name"]),
                    str(row["qname"]),
                    str(row["kind"]),
                    str(row["file"]),
                    max(int(row["sl"]), 1),
                    max(int(row["sc"]), 1),
                    max(int(row["el"]), 1),
                    max(int(row["ec"]), 1),
                    cast(str | None, row.get("sig")),
                    str(row["vis"]),
                    int(row["def"]),
                    boundary,
                )
            )
        elif kind == "ref" and row["file"] in known:
            references.append(
                (
                    str(row["file"]),
                    cast(str | None, row.get("from")),
                    str(row["to"]),
                    str(row["kind"]),
                    max(int(row["line"]), 1),
                    max(int(row["col"]), 1),
                )
            )
        elif kind == "dep" and row["from"] in known and row["to"] in known:
            dependencies.append((str(row["from"]), str(row["to"]), "import"))
    meta = IndexMeta(
        adapter="java",
        adapter_version=adapter_version,
        stats={
            "files": len(files),
            "symbols": len(symbols),
            "references": len(references),
            "dependencies": len(dependencies),
        },
        timings={"index_s": round(time.monotonic() - started, 3)},
        uncompiled=uncompiled,
    )
    path = ctx.cache_dir / INDEX_FILE_NAME
    with IndexStore.open(path, reset=True) as store:
        store.write(files=files, symbols=symbols, references=references, dependencies=dependencies)
        store.set_meta(meta)
    if problems:
        log.warning("the Java index helper reported %d problem(s): %s", len(problems), problems[0])
    return SymbolIndex(path=path, meta=meta, problems=problems[:50])
