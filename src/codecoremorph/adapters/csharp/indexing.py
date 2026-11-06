"""Host side of C# indexing (SPEC §7.2, §11.3, D-65): the Roslyn helper runs in the sandbox on
the files each project compiles, and its JSON lines become the SQLite index. The project files,
solutions and MSBuild's other files are build files.

The helper is compiled from its source into the adapter's cache with the SDK's own ``csc``,
against the SDK's Roslyn assemblies, once per helper version; it runs on the SDK's runtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
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

HELPER_SOURCE = "CsIndexHelper.cs"
HELPER_NAME = "CsIndexHelper"
_PUBLIC = ("public", "protected")
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
) -> SymbolIndex:
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
        files.append(
            file_row(
                ctx.worktree, path, role=role, language="csharp", public=public_api.matches(path)
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
        store.set_meta(meta)
    if problems:
        log.warning("the C# index helper reported %d problem(s): %s", len(problems), problems[0])
    return SymbolIndex(path=path, meta=meta, problems=problems[:50])
