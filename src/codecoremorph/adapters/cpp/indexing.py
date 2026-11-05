"""Host side of C++ indexing: run the libclang helper in the sandbox, then load its JSON lines
into the SQLite index (SPEC §7.2, FR-2.1, FR-2.3 to FR-2.5)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    Boundary,
    BuildTarget,
    FileRole,
    IndexMeta,
    LineEnding,
    SymbolIndex,
    TargetKind,
)
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

HELPER_NAME = "cpp_index_helper.py"
COMPILE_COMMANDS = "compile_commands.json"
HEADER_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tcc")
SOURCE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cp")
_BUILD_DIR_NAMES = {".git", ".codecoremorph", "build", "out", "cmake-build-debug"}


def install_helper(cache_dir: Path) -> Path:
    """Copy the helper into the cache directory, which the sandbox can see."""
    source = resources.files("codecoremorph.adapters.cpp.helpers").joinpath(HELPER_NAME)
    data = source.read_bytes()
    target = cache_dir / "helpers" / HELPER_NAME
    if not target.is_file() or target.read_bytes() != data:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
    return target


def python_for(ctx: AdapterContext) -> str:
    """The interpreter that has the libclang binding: ours natively, the image's in Docker."""
    return sys.executable if ctx.executor.kind == "native" else "python3"


def line_ending(data: bytes) -> LineEnding:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return LineEnding.MIXED
    if crlf:
        return LineEnding.CRLF
    return LineEnding.LF if lf else LineEnding.NONE


class FileClassifier:
    def __init__(self, ctx: AdapterContext):
        self.worktree = ctx.worktree
        self.tests = PathMatcher(ctx.lang.test_dirs)
        self.exclude = PathMatcher(ctx.lang.exclude)
        self.public_api = PathMatcher(ctx.lang.public_api)

    def row(self, path: str, *, c_compatible: bool, language: str | None = None) -> FileRow | None:
        host = self.worktree / path
        try:
            data = host.read_bytes()
        except OSError:
            return None
        try:
            encoding = TextFile.decode(data).encoding
        except BinaryFileError:
            encoding = "binary"
        lowered = path.lower()
        if language is None:
            language = "c" if lowered.endswith(".c") else "cpp"
        if self.exclude.matches(path):
            role = FileRole.THIRD_PARTY
        elif language == "cmake":
            role = FileRole.BUILD
        elif self.tests.matches(path):
            role = FileRole.TEST
        elif lowered.endswith(HEADER_EXTENSIONS):
            role = FileRole.HEADER
        else:
            role = FileRole.SOURCE
        return (
            path,
            language,
            hashlib.sha256(data).hexdigest(),
            role.value,
            int(c_compatible),
            int(self.public_api.matches(path)),
            encoding,
            line_ending(data).value,
        )


def _walk(worktree: Path, exclude: PathMatcher, wanted: Callable[[str], bool]) -> list[str]:
    found: list[str] = []
    for root, dirs, files in os.walk(worktree):
        dirs[:] = sorted(d for d in dirs if d not in _BUILD_DIR_NAMES)
        rel_root = Path(root).relative_to(worktree).as_posix()
        for name in sorted(files):
            if wanted(name):
                rel = name if rel_root == "." else f"{rel_root}/{name}"
                if not exclude.matches(rel):
                    found.append(rel)
    return found


def build_files(worktree: Path, exclude: PathMatcher) -> list[str]:
    """CMakeLists.txt and *.cmake files in the worktree (they belong to project-kind units)."""
    return _walk(worktree, exclude, lambda n: n == "CMakeLists.txt" or n.endswith(".cmake"))


def code_files(worktree: Path, exclude: PathMatcher) -> list[str]:
    """Every C and C++ source and header in the worktree, outside the excluded paths."""
    extensions = SOURCE_EXTENSIONS + HEADER_EXTENSIONS
    return _walk(worktree, exclude, lambda n: n.lower().endswith(extensions))


def run_index(ctx: AdapterContext, *, adapter_version: str, jobs: int | None = None) -> SymbolIndex:
    compdb = ctx.cache_dir / COMPILE_COMMANDS
    if not compdb.is_file():
        raise SandboxError(
            "the C++ project must be configured before it is indexed",
            reason=f"{compdb} doesn't exist",
            hint="Run configure first (codecoremorph debug index does).",
        )
    helper = install_helper(ctx.cache_dir)
    work = ctx.cache_dir / "index"
    work.mkdir(parents=True, exist_ok=True)
    output = work / "output.jsonl"
    output.unlink(missing_ok=True)
    request = {
        "workspace": ctx.sandbox(ctx.worktree),
        "compile_commands": ctx.sandbox(compdb),
        "output": ctx.sandbox(output),
        "cache_dir": ctx.sandbox(work / "tu-cache"),
        "exclude": list(ctx.lang.exclude),
        "files": None,
        "jobs": jobs,
        "cmake_build_dir": ctx.sandbox(ctx.build_dir),
    }
    request_path = work / "request.json"
    request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

    started = time.monotonic()
    result = ctx.executor.run(
        [python_for(ctx), "-I", ctx.sandbox(helper), ctx.sandbox(request_path)],
        cwd=ctx.sandbox(ctx.worktree),
        timeout_s=ctx.lang.build_timeout_s,
        log_path=ctx.log_dir / "index.log",
    )
    helper_s = time.monotonic() - started
    if not result.ok or not output.is_file():
        _, stderr = full_output(result)
        raise SandboxError(
            "the C++ indexer failed" + (" (timed out)" if result.timed_out else ""),
            reason=stderr.strip().splitlines()[-1] if stderr.strip() else None,
            hint=f"See the full log: {result.log_path}",
        )

    started = time.monotonic()
    index = ingest(ctx, output, adapter_version=adapter_version, helper_s=helper_s)
    log.info(
        "indexed %s files, %s symbols in %.1f s (%s translation units parsed, %s cached)",
        index.meta.stats.get("files"),
        index.meta.stats.get("symbols"),
        helper_s + time.monotonic() - started,
        index.meta.stats.get("parsed"),
        index.meta.stats.get("cached"),
    )
    return index


def ingest(
    ctx: AdapterContext, output: Path, *, adapter_version: str, helper_s: float = 0.0
) -> SymbolIndex:
    started = time.monotonic()
    classifier = FileClassifier(ctx)
    c_files: dict[str, bool] = {}
    symbols: list[SymbolRow] = []
    references: list[ReferenceRow] = []
    dependencies: list[DependencyRow] = []
    problems: list[str] = []
    targets: list[BuildTarget] = []
    meta: dict[str, object] = {}
    with output.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            tag = record[0]
            if tag == "sym":
                symbols.append(tuple(record[1:]))  # type: ignore[arg-type]
            elif tag == "ref":
                references.append(tuple(record[1:]))  # type: ignore[arg-type]
            elif tag == "dep":
                dependencies.append((record[1], record[2], "include"))
            elif tag == "file":
                c_files[record[1]] = c_files.get(record[1], False) or bool(record[2])
            elif tag == "problem":
                problems.append(str(record[1]))
            elif tag == "target":
                targets.append(build_target(record[1], record[2], record[3]))
            elif tag == "meta":
                meta = dict(record[1])

    files: list[FileRow] = []
    for path, c_tu in sorted(c_files.items()):
        is_c_source = path.lower().endswith(".c")
        row = classifier.row(path, c_compatible=c_tu and not is_c_source)
        if row is not None:
            files.append(row)
    for path in build_files(ctx.worktree, classifier.exclude):
        row = classifier.row(path, c_compatible=False, language="cmake")
        if row is not None:
            files.append(row)
    # Code no compile command reaches (another platform's sources, disabled targets): builds
    # and tests can't check changes to it, so units and reports name it (D-42).
    uncompiled = [
        p
        for p in code_files(ctx.worktree, classifier.exclude)
        if p not in c_files and not p.lower().endswith(".c")  # C sources: no C++ rule applies
    ]
    public = {row[0] for row in files if row[5]}
    symbols = [_with_public_api(row, public) for row in symbols]

    store = IndexStore.open(ctx.cache_dir / INDEX_FILE_NAME, reset=True)
    try:
        store.write(files=files, symbols=symbols, references=references, dependencies=dependencies)
        counts = store.counts()
        index_meta = IndexMeta(
            adapter="cpp",
            adapter_version=adapter_version,
            tool_versions={"libclang": str(meta.get("libclang", "unknown"))},
            stats={
                "translation_units": _int(meta.get("translation_units")),
                "parsed": _int(meta.get("parsed")),
                "cached": _int(meta.get("cached")),
                "failed": _int(meta.get("failed")),
                "files": counts["files"],
                "symbols": counts["symbols"],
                "references": counts["refs"],
                "dependencies": counts["deps"],
            },
            timings={
                "helper_s": round(helper_s, 3),
                "parse_s": float(str(meta.get("seconds", 0.0))),
                "ingest_s": round(time.monotonic() - started, 3),
            },
            uncompiled=uncompiled,
        )
        store.set_meta(index_meta)
        store.set_build_targets(targets)
    finally:
        store.close()
    return SymbolIndex(path=ctx.cache_dir / INDEX_FILE_NAME, meta=index_meta, problems=problems)


_LIBRARY_TYPES = frozenset(
    {"STATIC_LIBRARY", "SHARED_LIBRARY", "MODULE_LIBRARY", "OBJECT_LIBRARY", "INTERFACE_LIBRARY"}
)


def build_target(name: object, cmake_type: object, files: object) -> BuildTarget:
    """A CMake target from the file API, as the planner sees it."""
    kind = (
        TargetKind.LIBRARY
        if cmake_type in _LIBRARY_TYPES
        else TargetKind.EXECUTABLE
        if cmake_type == "EXECUTABLE"
        else TargetKind.OTHER
    )
    paths = [str(f) for f in files] if isinstance(files, list) else []
    return BuildTarget(name=str(name), kind=kind, files=paths)


def _with_public_api(row: SymbolRow, public: set[str]) -> SymbolRow:
    if row[4] in public and row[12] == Boundary.NONE.value:
        return (*row[:12], Boundary.PUBLIC_API.value)
    return row


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
