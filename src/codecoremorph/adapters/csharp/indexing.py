"""Host side of C# indexing (SPEC §7.2, §11.3, D-65): the Roslyn helper runs in the sandbox on
the files each project compiles, and its JSON lines become the SQLite index. The project files,
solutions and MSBuild's other files are build files.

The source of a port (``LanguageConfig.port_target``, D-78) also gets its types, the uses of its
generics, what a port refuses, the exceptions its members throw, and its test methods with the
TRX names of their cases (:func:`port_records`).

The helper is compiled from its source into the adapter's cache with the SDK's own ``csc``,
against the SDK's Roslyn assemblies, once per helper version; it runs on the SDK's runtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, cast

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.csharp.dotnet import DotnetToolchain
from codecoremorph.adapters.csharp.msbuild import (
    Layout,
    inherited_properties,
    project_info,
)
from codecoremorph.adapters.csharp.trx import test_id
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models import (
    BuildTarget,
    FileRole,
    GenericUse,
    IndexMeta,
    LineEnding,
    NativeImport,
    NativeLibraryProbe,
    PackageEntry,
    PackageUse,
    PortNotice,
    PortNoticeKind,
    PortRefusal,
    RefusalSite,
    SymbolIndex,
    SymbolKind,
    TargetKind,
    TestEntry,
    TestMethod,
    ThrowSite,
    TypeEntry,
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

HELPER_SOURCE = "CsIndexHelper.cs"
HELPER_NAME = "CsIndexHelper"
_PUBLIC = ("public", "protected")
# A generic used all over keeps this many of its uses with each set of type arguments: enough
# for a unit's note, without one row per member access.
MAX_USES = 12
_RUNTIME_CONFIG = {
    "runtimeOptions": {
        "tfm": "net10.0",
        "framework": {"name": "Microsoft.NETCore.App", "version": "10.0.0"},
        "rollForward": "LatestMajor",
    }
}


def line_ending(data: bytes) -> LineEnding:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return LineEnding.MIXED
    if crlf:
        return LineEnding.CRLF
    return LineEnding.LF if lf else LineEnding.NONE


def framework_defines(tfm: str) -> list[str]:
    """The preprocessor symbols the SDK defines for ``tfm`` (``NET6_0``, ``NET5_0_OR_GREATER``,
    ``NETSTANDARD2_0``), with ``DEBUG`` and ``TRACE``."""
    defines = ["DEBUG", "TRACE"]
    text = tfm.strip().lower().split("-", 1)[0]
    if match := re.fullmatch(r"net(\d+)\.(\d+)", text):
        major, minor = int(match.group(1)), int(match.group(2))
        if major >= 5:
            defines += ["NET", "NETCOREAPP", f"NET{major}_{minor}"]
            defines += [f"NET{v}_0_OR_GREATER" for v in range(5, major + 1)]
            defines += [f"NETCOREAPP{v}_OR_GREATER" for v in ("1_0", "2_0", "2_1", "3_0", "3_1")]
            return defines
    if match := re.fullmatch(r"netcoreapp(\d+)\.(\d+)", text):
        major, minor = int(match.group(1)), int(match.group(2))
        defines += ["NETCOREAPP", f"NETCOREAPP{major}_{minor}"]
        versions = [(1, 0), (1, 1), (2, 0), (2, 1), (2, 2), (3, 0), (3, 1)]
        defines += [
            f"NETCOREAPP{a}_{b}_OR_GREATER" for a, b in versions if (a, b) <= (major, minor)
        ]
        return defines
    if match := re.fullmatch(r"netstandard(\d+)\.(\d+)", text):
        major, minor = int(match.group(1)), int(match.group(2))
        defines += ["NETSTANDARD", f"NETSTANDARD{major}_{minor}"]
        versions = [(1, v) for v in range(7)] + [(2, 0), (2, 1)]
        defines += [
            f"NETSTANDARD{a}_{b}_OR_GREATER" for a, b in versions if (a, b) <= (major, minor)
        ]
        return defines
    if match := re.fullmatch(r"net([1-4])(\d)(\d?)", text):
        defines += ["NETFRAMEWORK", f"NET{match.group(1)}{match.group(2)}{match.group(3)}"]
    return defines


def _text(path: Path) -> str | None:
    try:
        return TextFile.decode(path.read_bytes()).text
    except (OSError, BinaryFileError):
        return None


def helper_assembly(ctx: AdapterContext, tools: DotnetToolchain) -> Path:
    """The compiled helper in the cache (``CsIndexHelper.dll`` and its runtime config),
    compiled again when its source changed."""
    source = resources.files("codecoremorph.adapters.csharp.helpers").joinpath(HELPER_SOURCE)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    base = ctx.cache_dir / "helpers" / "csharp"
    assembly = base / f"{HELPER_NAME}.dll"
    stamp = base / ".source-sha256"
    if (
        stamp.is_file()
        and stamp.read_text(encoding="utf-8").strip() == digest
        and assembly.is_file()
    ):
        return assembly
    base.mkdir(parents=True, exist_ok=True)
    (base / HELPER_SOURCE).write_bytes(data)
    (base / f"{HELPER_NAME}.runtimeconfig.json").write_text(
        json.dumps(_RUNTIME_CONFIG), encoding="utf-8"
    )
    # csc reads its arguments from a response file: the reference pack's assemblies, by name.
    response = base / "csc.rsp"
    lines = [
        "-nologo",
        "-nostdlib",
        "-nullable:enable",
        "-langversion:latest",
        "-target:exe",
        "-optimize",
        f'-out:"{ctx.sandbox(assembly)}"',
        f'-lib:"{tools.ref_pack}"',
        f'-r:"{tools.join(tools.roslyn, "Microsoft.CodeAnalysis.dll")}"',
        f'-r:"{tools.join(tools.roslyn, "Microsoft.CodeAnalysis.CSharp.dll")}"',
        *[f"-r:{name}" for name in _REFERENCE_ASSEMBLIES],
        f'"{ctx.sandbox(base / HELPER_SOURCE)}"',
    ]
    response.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = ctx.executor.run(
        [tools.dotnet, "exec", tools.join(tools.roslyn, "csc.dll"), f"@{ctx.sandbox(response)}"],
        cwd=ctx.sandbox(base),
        env={"DOTNET_CLI_UI_LANGUAGE": "en"},
        timeout_s=300,
        log_path=ctx.log_dir / "index-helper-csc.log",
    )
    if not result.ok or not assembly.is_file():
        stdout, stderr = full_output(result)
        raise SandboxError(
            "couldn't compile the C# index helper",
            reason="\n".join((stdout + stderr).strip().splitlines()[-10:]) or None,
        )
    stamp.write_text(digest, encoding="utf-8")
    return assembly


# The framework assemblies the helper uses (csc finds them in the reference pack, -lib).
_REFERENCE_ASSEMBLIES = (
    "System.Runtime.dll",
    "System.Collections.dll",
    "System.Collections.Immutable.dll",
    "System.Console.dll",
    "System.Linq.dll",
    "System.Text.Json.dll",
    "System.Text.Encoding.Extensions.dll",
    "System.Memory.dll",
    "System.Runtime.Extensions.dll",
    "System.Reflection.Metadata.dll",
    "System.Runtime.InteropServices.dll",
    "netstandard.dll",
)


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


def run_index(
    ctx: AdapterContext,
    tools: DotnetToolchain,
    lay: Layout,
    *,
    mirror: Path,
    adapter_version: str,
    port: bool = False,
) -> SymbolIndex:
    """The index of the projects' compiled files; ``port`` adds what a port's source needs
    (D-78)."""
    started = time.monotonic()
    ctx.cache_dir.mkdir(parents=True, exist_ok=True)
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    tests = PathMatcher(ctx.lang.test_dirs)
    public_api = PathMatcher(ctx.lang.public_api)
    compiled = lay.compiled
    work = ctx.cache_dir / "index-run"
    work.mkdir(parents=True, exist_ok=True)
    output = work / "index.jsonl"
    output.unlink(missing_ok=True)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    if compiled:
        assembly = helper_assembly(ctx, tools)
        request = work / "request.txt"
        lines = [
            f"root={ctx.sandbox(ctx.worktree)}",
            f"output={ctx.sandbox(output)}",
            f"refpack={tools.ref_pack}",
        ]
        if port:
            lines.append("port=1")
        for project in lay.projects:
            info = project_info(
                _text(ctx.worktree / project) or "", inherited_properties(ctx.worktree, project)
            )
            lines.append(f"project={project}")
            lines += [f"source={f}" for f in lay.sources.get(project, [])]
            first = info.frameworks[0] if info.frameworks else ""
            lines += [f"define={d}" for d in framework_defines(first)]
            assets = mirror / PurePosixPath(project).parent / "obj" / "project.assets.json"
            if assets.is_file():
                lines.append(f"assets={ctx.sandbox(assets)}")
            lines += [f"reference={r}" for r in lay.references.get(project, [])]
        request.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = ctx.executor.run(
            [tools.dotnet, "exec", ctx.sandbox(assembly), tools.roslyn, ctx.sandbox(request)],
            cwd=ctx.sandbox(work),
            env={"DOTNET_CLI_UI_LANGUAGE": "en"},
            timeout_s=ctx.lang.build_timeout_s,
            log_path=ctx.log_dir / "index-helper.log",
        )
        if not result.ok or not output.is_file():
            stdout, stderr = full_output(result)
            raise SandboxError(
                "the C# index helper failed",
                reason="\n".join((stdout + stderr).strip().splitlines()[-15:]) or None,
                hint=f"See {result.log_path}.",
            )
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(cast(dict[str, Any], json.loads(line)))
    files: list[FileRow] = []
    for path in sorted({*lay.projects, *lay.build_files}):
        files.append(
            file_row(ctx.worktree, path, role=FileRole.BUILD, language="msbuild", public=False)
        )
    for path in compiled:
        role = FileRole.TEST if tests.matches(path) else FileRole.SOURCE
        # A port's source files are its façades' API, which I-6 snapshots (D-78).
        public = public_api.matches(path) or (port and role is FileRole.SOURCE)
        files.append(file_row(ctx.worktree, path, role=role, language="csharp", public=public))
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
            # A port's source keeps its API behind façades: I-6 checks each member's signature
            # (D-78), as the manifest's public_api asks of an upgrade.
            exposed = public_api.matches(row["file"]) or (port and not tests.matches(row["file"]))
            if boundary == "none" and row["vis"] in _PUBLIC and exposed:
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
        adapter="csharp",
        adapter_version=adapter_version,
        stats={
            "files": len(files),
            "symbols": len(symbols),
            "references": len(references),
            "dependencies": len(dependencies),
        },
        timings={"index_s": round(time.monotonic() - started, 3)},
        uncompiled=lay.uncompiled,
    )
    path = ctx.cache_dir / INDEX_FILE_NAME
    with IndexStore.open(path, reset=True) as store:
        store.write(files=files, symbols=symbols, references=references, dependencies=dependencies)
        if port:
            records = port_records(rows, known)
            store.set_port_source(
                types=records.types,
                generic_uses=records.uses,
                test_methods=records.tests,
                packages=records.packages,
            )
            store.set_test_entries(records.entries())
            store.set_build_targets(project_targets(ctx.worktree, lay))
            libraries = sorted({i.library for t in records.types for i in t.native_imports})
            store.set_native_probes(probe_native(ctx, libraries))
            meta.stats["types"] = len(records.types)
            meta.stats["test_methods"] = len(records.tests)
        store.set_meta(meta)
    if problems:
        log.warning("the C# index helper reported %d problem(s): %s", len(problems), problems[0])
    return SymbolIndex(path=path, meta=meta, problems=problems[:50])


def project_targets(worktree: Path, lay: Layout) -> list[BuildTarget]:
    """Each project as a build target (D-78): a test project, a program (``OutputType`` Exe or
    WinExe) or a library, with the files it compiles and the projects it references. A port
    cuts its types from the libraries, and leaves programs and tests to their own units."""
    targets: list[BuildTarget] = []
    for project in sorted(lay.projects):
        info = project_info(
            _text(worktree / project) or "", inherited_properties(worktree, project)
        )
        if info.test_project:
            kind = TargetKind.TEST
        elif info.executable:
            kind = TargetKind.EXECUTABLE
        else:
            kind = TargetKind.LIBRARY
        targets.append(
            BuildTarget(
                name=project,
                kind=kind,
                files=list(lay.sources.get(project, [])),
                dependencies=sorted(lay.references.get(project, [])),
                checked_arithmetic=info.checked_arithmetic,
            )
        )
    return targets


@dataclass
class PortRecords:
    """What a port's source records beyond its symbols (D-78)."""

    types: list[TypeEntry] = field(default_factory=list[TypeEntry])
    uses: list[GenericUse] = field(default_factory=list[GenericUse])
    tests: list[TestMethod] = field(default_factory=list[TestMethod])
    packages: list[PackageEntry] = field(default_factory=list[PackageEntry])

    def entries(self) -> list[TestEntry]:
        """A test entry per case the sources name exactly, run by its test project."""
        return [
            TestEntry(test_id=case, runs=method.project, file=method.file)
            for method in self.tests
            if method.exact
            for case in method.cases
        ]


# For each library a P/Invoke names, where the toolchain image has its C header, the library to
# link and its pkg-config version: the new code includes and links it (D-82). One line each:
# name, header, library, version, tab-separated (empty when not found).
_NATIVE_PROBE = r"""for name in "$@"; do
  base=${name#lib}; base=${base%.so*}; base=${base%.dll}; base=${base%.dylib}
  header=""; link=""; version=""
  for d in /usr/local/include /usr/include; do
    for h in "$d/$base.h" "$d/$base/$base.h" "$d/lib$base.h"; do
      if [ -z "$header" ] && [ -f "$h" ]; then header=$h; fi
    done
  done
  for d in /usr/local/lib /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu \
      /usr/lib64 /usr/lib; do
    for l in "$d/lib$base.so" "$d/lib$base.a"; do
      if [ -z "$link" ] && [ -e "$l" ]; then link=$l; fi
    done
    for pc in "$d/pkgconfig/lib$base.pc" "$d/pkgconfig/$base.pc"; do
      if [ -z "$version" ] && [ -f "$pc" ]; then version=$(sed -n 's/^Version: *//p' "$pc"); fi
    done
  done
  printf '%s\t%s\t%s\t%s\n' "$name" "$header" "$link" "$version"
done
"""


def probe_native(ctx: AdapterContext, libraries: Sequence[str]) -> list[NativeLibraryProbe]:
    """What the toolchain image has of each native library (D-82). Only in Docker, where the
    toolchain is the image's: natively there is no one place to look, so nothing is said."""
    if not libraries or ctx.executor.kind != "docker":
        return []
    ctx.log_dir.mkdir(parents=True, exist_ok=True)
    result = ctx.executor.run(
        ["sh", "-c", _NATIVE_PROBE, "probe", *libraries],
        cwd=ctx.sandbox(ctx.worktree),
        timeout_s=60,
        log_path=ctx.log_dir / "native-probe.log",
    )
    stdout, _ = full_output(result)
    found: list[NativeLibraryProbe] = []
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or parts[0] not in libraries:
            continue
        found.append(
            NativeLibraryProbe(
                library=parts[0],
                header=parts[1] or None,
                link=parts[2] or None,
                version=parts[3] or None,
            )
        )
    return found


def port_records(rows: Iterable[Mapping[str, Any]], known: Collection[str]) -> PortRecords:
    """The helper's ``type``, ``inst``, ``refuse``, ``throw``, ``test``, ``notice``, ``native``,
    ``pkguse`` and ``pkg`` rows (``port=1``) as the index's models, for the files the index has
    (``known``). A refusal, a throw, a notice, a P/Invoke declaration or a package use goes to
    the type it is in; a generic's uses with the same type arguments keep their first
    :data:`MAX_USES`."""
    types: dict[str, TypeEntry] = {}
    uses: list[GenericUse] = []
    tests: list[TestMethod] = []
    packages: list[PackageEntry] = []
    refusals: list[tuple[str, RefusalSite]] = []
    throws: list[tuple[str, ThrowSite]] = []
    notices: list[tuple[str, PortNotice]] = []
    imports: list[tuple[str, NativeImport]] = []
    package_uses: list[tuple[str, PackageUse]] = []
    counts: dict[tuple[str, tuple[str, ...]], int] = {}
    for row in rows:
        kind = row.get("t")
        if kind == "type":
            files = [str(f) for f in _strings(row.get("files")) if f in known]
            if not files:
                continue
            types[str(row["id"])] = TypeEntry(
                id=str(row["id"]),
                name=str(row["name"]),
                kind=SymbolKind(str(row["kind"])),
                files=files,
                project=_optional(row.get("project")),
                outer=_optional(row.get("outer")),
                base=_optional(row.get("base")),
                interfaces=_strings(row.get("interfaces")),
                external_bases=_strings(row.get("external")),
                abstract=bool(row.get("abstract")),
                sealed=bool(row.get("sealed")),
                static=bool(row.get("static")),
                type_parameters=_strings(row.get("tparams")),
                members=_strings(row.get("members")),
                virtual_members=_strings(row.get("virtual")),
                instance_fields=_strings(row.get("fields")),
                static_state=_strings(row.get("statics")),
                native_only=bool(row.get("native_only")),
            )
        elif kind == "refuse" and row.get("file") in known:
            site = RefusalSite(
                what=PortRefusal(str(row["what"])),
                detail=str(row.get("detail") or row["what"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            refusals.append((str(row["type"]), site))
        elif kind == "notice" and row.get("file") in known:
            notice = PortNotice(
                what=PortNoticeKind(str(row["what"])),
                detail=str(row.get("detail") or row["what"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            notices.append((str(row["type"]), notice))
        elif kind == "native" and row.get("file") in known:
            native = NativeImport(
                library=str(row.get("library") or ""),
                entry_point=str(row["entry"]),
                member=str(row["member"]),
                signature=str(row.get("sig") or ""),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
            )
            imports.append((str(row["type"]), native))
        elif kind == "pkguse" and row.get("file") in known:
            used = PackageUse(
                package=str(row["package"]),
                symbol=str(row["symbol"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            package_uses.append((str(row["type"]), used))
        elif kind == "pkg":
            packages.append(
                PackageEntry(
                    project=str(row["project"]),
                    name=str(row["name"]),
                    version=str(row.get("version") or ""),
                    assemblies=_strings(row.get("assemblies")),
                    native=_strings(row.get("native")),
                )
            )
        elif kind == "throw" and row.get("file") in known:
            thrown = ThrowSite(
                exception=str(row["exception"]),
                file=str(row["file"]),
                line=max(int(row["line"]), 1),
                member=_optional(row.get("member")),
            )
            throws.append((str(row["type"]), thrown))
        elif kind == "inst" and row.get("file") in known:
            arguments = _strings(row.get("args"))
            key = (str(row["of"]), tuple(arguments))
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > MAX_USES:
                continue
            uses.append(
                GenericUse(
                    symbol=str(row["of"]),
                    arguments=arguments,
                    open=bool(row.get("open")),
                    file=str(row["file"]),
                    line=max(int(row["line"]), 1),
                    member=_optional(row.get("member")),
                )
            )
        elif kind == "test" and row.get("file") in known:
            class_name = str(row["cls"])
            tests.append(
                TestMethod(
                    file=str(row["file"]),
                    class_name=class_name,
                    method=str(row["method"]),
                    framework=str(row["fw"]),
                    project=_optional(row.get("project")),
                    cases=[test_id(class_name, c) for c in _strings(row.get("cases"))],
                    exact=bool(row.get("exact", True)),
                )
            )
    for owner, site in refusals:
        if owner in types:
            types[owner].refusals.append(site)
    for owner, thrown in throws:
        if owner in types:
            types[owner].throws.append(thrown)
    for owner, notice in notices:
        if owner in types:
            types[owner].notices.append(notice)
    for owner, native in imports:
        if owner in types:
            types[owner].native_imports.append(native)
    for owner, used in package_uses:
        if owner in types:
            types[owner].package_uses.append(used)
    return PortRecords(
        types=sorted(types.values(), key=lambda t: t.id),
        uses=sorted(uses, key=lambda u: (u.symbol, u.file, u.line, u.arguments)),
        tests=sorted(tests, key=lambda m: (m.file, m.class_name, m.method)),
        packages=sorted(packages, key=lambda p: (p.project, p.name)),
    )


def _optional(value: object) -> str | None:
    return str(value) if value else None


def _strings(value: object) -> list[str]:
    return [str(v) for v in cast(list[object], value)] if isinstance(value, list) else []
