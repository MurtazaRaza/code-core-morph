"""What a Make build tells the planner, from the compiler and archiver calls it made (D-68).

CMake describes its targets (the file API, D-59); a Makefile doesn't. So a Make build runs with
``helpers/capture.sh`` installed as its ``cc``, ``c++`` and ``ar``: each call leaves a file in
the capture directory with its working directory and arguments. :func:`read_calls` reads them
in the order they ran, and :func:`build_model` turns them into what the index and the planner
need: the compile commands (``compile_commands.json``) and the build targets, libraries (an
archive and a shared library made of the same sources are one target) and programs with the
project libraries they link. Later builds add calls: the newest compile of a source and the
newest archive or link of an output win, so an incremental build updates the model.

Paths here are the sandbox's (POSIX, inside the mirror the build ran in); :func:`for_worktree`
turns the compile commands into the worktree's.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codecoremorph.core.models import BuildTarget, TargetKind

SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".c++", ".C", ".s", ".S")
_OBJECT_SUFFIXES = (".o", ".lo", ".obj")
# Compiler options whose value is the next argument.
_TAKES_VALUE = frozenset(
    {
        "-o", "-I", "-D", "-U", "-include", "-imacros", "-isystem", "-iquote", "-idirafter",
        "-MF", "-MT", "-MQ", "-x", "-L", "-l", "-Xlinker", "-Xassembler", "-Xpreprocessor",
        "-arch", "-target", "--sysroot", "-isysroot", "-u", "-z", "-T", "-e",
    }
)  # fmt: skip
_INCLUDE_OPTIONS = ("-I", "-isystem", "-iquote", "-idirafter")


@dataclass(frozen=True)
class Call:
    """One captured call: ``tool`` is ``cc``, ``cxx`` or ``ar``."""

    tool: str
    cwd: str
    args: tuple[str, ...]


def read_calls(directory: Path) -> list[Call]:
    """The calls logged in ``directory``, oldest first (by modification time, then name)."""
    if not directory.is_dir():
        return []
    logs = sorted(directory.iterdir(), key=lambda p: (p.stat().st_mtime_ns, p.name))
    calls: list[Call] = []
    for path in logs:
        tool = path.name.split(".", 1)[0]
        parts = path.read_bytes().split(b"\0")
        if parts and parts[-1] == b"":
            parts.pop()
        if tool not in ("cc", "cxx", "ar") or not parts:
            continue
        text = [p.decode("utf-8", "surrogateescape") for p in parts]
        calls.append(Call(tool=tool, cwd=text[0], args=tuple(text[1:])))
    return calls


def _absolute(cwd: str, path: str) -> str:
    return posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd, path))


@dataclass
class _Compiler:
    out: str | None = None
    sources: list[str] = field(default_factory=list[str])
    inputs: list[str] = field(default_factory=list[str])
    libdirs: list[str] = field(default_factory=list[str])
    libs: list[str] = field(default_factory=list[str])
    flags: set[str] = field(default_factory=set[str])


def _parse_compiler(call: Call) -> _Compiler:
    parsed = _Compiler()
    args = call.args
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _TAKES_VALUE:
            value = args[i + 1] if i + 1 < len(args) else ""
            if arg == "-o":
                parsed.out = _absolute(call.cwd, value)
            elif arg == "-L":
                parsed.libdirs.append(_absolute(call.cwd, value))
            elif arg == "-l":
                parsed.libs.append(value)
            i += 2
            continue
        if arg.startswith("-o") and len(arg) > 2:
            parsed.out = _absolute(call.cwd, arg[2:])
        elif arg.startswith("-L") and len(arg) > 2:
            parsed.libdirs.append(_absolute(call.cwd, arg[2:]))
        elif arg.startswith("-l") and len(arg) > 2:
            parsed.libs.append(arg[2:])
        elif arg.startswith("-"):
            parsed.flags.add(arg)
        elif arg.endswith(SOURCE_SUFFIXES):
            parsed.sources.append(_absolute(call.cwd, arg))
        else:
            parsed.inputs.append(_absolute(call.cwd, arg))
        i += 1
    return parsed


def library_name(path: str) -> str:
    """``libbz2.a`` -> ``bz2``, ``libz.so.1.2.11`` -> ``z``, ``.libs/libyaml-0.so.2`` ->
    ``yaml-0``."""
    file_name = posixpath.basename(path)
    base = file_name.removeprefix("lib")
    for suffix in (".a", ".so", ".dylib", ".dll", ".lib"):
        if suffix in base:
            base = base[: base.index(suffix)]
    return base or file_name  # "lib.a" names no library: keep the file's name


def _object_for(cwd: str, source: str) -> str:
    stem = posixpath.splitext(posixpath.basename(source))[0]
    return _absolute(cwd, stem + ".o")


def _program_path(out: str) -> str:
    """Where a program is run from: libtool links one into ``.libs/`` (``.libs/lt-name`` when it
    relinks) and puts a wrapper script at ``name``, which is what make makes and recipes run."""
    directory, name = posixpath.split(out)
    if posixpath.basename(directory) != ".libs":
        return out
    return posixpath.join(posixpath.dirname(directory), name.removeprefix("lt-"))


# A partial link (``cc -r``) makes an object, not a program: a C port's crate becomes one
# before libtool archives it (D-70).
_PARTIAL_LINK = frozenset({"-r", "--relocatable", "-Wl,-r", "-Wl,--relocatable"})


@dataclass
class BuildModel:
    """What the captured calls say: ``commands`` in ``compile_commands.json`` form (sandbox
    paths) and ``targets`` (paths relative to ``root``)."""

    commands: list[dict[str, Any]]
    targets: list[BuildTarget]


@dataclass
class _Link:
    out: str
    shared: bool
    objects: list[str]
    libraries: list[str]
    libdirs: list[str]
    libs: list[str]


def build_model(calls: Iterable[Call], *, root: str, compilers: dict[str, str]) -> BuildModel:
    """The model of the build in ``root`` (the sandbox path of the mirror) from ``calls``,
    oldest first. ``compilers`` maps ``cc`` and ``cxx`` to the real compilers, which the
    compile commands name."""

    def rel(path: str) -> str | None:
        r = posixpath.relpath(path, root)
        return None if r == ".." or r.startswith("../") else r

    commands: dict[str, dict[str, Any]] = {}  # source -> its newest compile
    object_source: dict[str, str] = {}
    archives: dict[str, list[str]] = {}  # archive -> members, its newest call
    links: dict[str, _Link] = {}  # output -> its newest link
    for call in calls:
        if call.tool == "ar":
            ops, rest = (call.args[0], call.args[1:]) if call.args else ("", ())
            if not any(c in ops.lstrip("-") for c in "rq") or not rest:
                continue  # listing or extracting, not making an archive
            archives[_absolute(call.cwd, rest[0])] = [_absolute(call.cwd, m) for m in rest[1:]]
            continue
        parsed = _parse_compiler(call)
        flags = parsed.flags
        if "-E" in flags or (("-M" in flags or "-MM" in flags) and "-c" not in flags):
            continue  # preprocessing or dependency listing only
        compiles = "-c" in flags
        for source in parsed.sources:
            if compiles and parsed.out and len(parsed.sources) == 1:
                obj = parsed.out
            else:
                obj = _object_for(call.cwd, source)
            object_source[obj] = source
            previous = commands.get(source)
            # libtool compiles each source twice, PIC and not: the first stands for both.
            if previous is None or previous["output"] == obj or not compiles:
                commands[source] = {
                    "directory": call.cwd,
                    "arguments": [compilers.get(call.tool, call.tool), *call.args],
                    "file": source,
                    "output": obj,
                }
        if not compiles and parsed.out and not flags & _PARTIAL_LINK:
            own = [_object_for(call.cwd, s) for s in parsed.sources]
            links[parsed.out] = _Link(
                out=parsed.out,
                shared="-shared" in flags,
                objects=own + [i for i in parsed.inputs if i.endswith(_OBJECT_SUFFIXES)],
                libraries=[i for i in parsed.inputs if not i.endswith(_OBJECT_SUFFIXES)],
                libdirs=parsed.libdirs,
                libs=parsed.libs,
            )

    def sources_of(objects: Iterable[str]) -> list[str]:
        found = {rel(object_source[o]) for o in objects if o in object_source}
        return sorted(f for f in found if f is not None)

    targets: dict[str, dict[str, Any]] = {}
    artifact_target: dict[str, str] = {}

    def target(name: str, kind: TargetKind) -> dict[str, Any]:
        return targets.setdefault(
            name, {"name": name, "kind": kind, "files": set(), "deps": set(), "artifacts": set()}
        )

    for archive, members in sorted(archives.items()):
        entry = target(library_name(archive), TargetKind.LIBRARY)
        entry["files"] |= set(sources_of(members))
        entry["artifacts"].add(rel(archive))
        artifact_target[archive] = entry["name"]
    for link in sorted((x for x in links.values() if x.shared), key=lambda x: x.out):
        files = set(sources_of(link.objects))
        same = [
            t for t in targets.values() if t["kind"] is TargetKind.LIBRARY and t["files"] == files
        ]
        entry = same[0] if same else target(library_name(link.out), TargetKind.LIBRARY)
        entry["files"] |= files
        entry["artifacts"].add(rel(link.out))
        artifact_target[link.out] = entry["name"]
    for link in sorted((x for x in links.values() if not x.shared), key=lambda x: x.out):
        program = _program_path(link.out)
        entry = target(posixpath.basename(program), TargetKind.EXECUTABLE)
        entry["files"] |= set(sources_of(link.objects))
        entry["artifacts"].add(rel(program))
        for library in link.libraries:
            name = artifact_target.get(library)
            if name is None:
                # libtool links lib.so, a link to the library it made (libyaml-0.so.2.0.9):
                # the project's library of that name, made in the same directory.
                name = next(
                    (
                        n
                        for artifact, n in artifact_target.items()
                        if n == library_name(library)
                        and posixpath.dirname(artifact) == posixpath.dirname(library)
                    ),
                    None,
                )
            if name is not None:
                entry["deps"].add(name)
        for lib in link.libs:
            for artifact, name in artifact_target.items():
                if posixpath.dirname(artifact) in link.libdirs and library_name(artifact) == lib:
                    entry["deps"].add(name)

    built: list[BuildTarget] = []
    for entry in sorted(targets.values(), key=lambda t: (t["kind"].value, t["name"])):
        built.append(
            BuildTarget(
                name=entry["name"],
                kind=entry["kind"],
                files=sorted(entry["files"]),
                dependencies=sorted(entry["deps"] - {entry["name"]}),
                artifacts=sorted(a for a in entry["artifacts"] if a is not None),
            )
        )
    return BuildModel(commands=sorted(commands.values(), key=lambda c: c["file"]), targets=built)


def for_worktree(
    commands: Iterable[dict[str, Any]], *, mirror: str, worktree: str, exists: Callable[[str], bool]
) -> list[dict[str, Any]]:
    """The compile commands for the worktree's files: each command compiles the worktree's
    copy of its file (``exists`` says which relative paths the worktree has), and also searches
    the mirror's directories, where configure's generated headers (``config.h``) are, after
    the worktree's (``-iquote`` for quoted includes, ``-idirafter`` for the rest)."""

    def moved(path: str) -> str:
        r = posixpath.relpath(path, mirror)
        if r == ".." or r.startswith("../"):
            return path
        return worktree if r == "." else posixpath.join(worktree, r)

    found: list[dict[str, Any]] = []
    for command in commands:
        r = posixpath.relpath(command["file"], mirror)
        if r.startswith("../") or not exists(r):
            continue  # a generated source, or one the worktree no longer has
        args: list[str] = list(command["arguments"])
        mirrored_dirs = [command["directory"]]
        rewritten: list[str] = [args[0]]
        i = 1
        while i < len(args):
            arg = args[i]
            option = next((o for o in _INCLUDE_OPTIONS if arg == o or arg.startswith(o)), None)
            if option is not None:
                value = args[i + 1] if arg == option and i + 1 < len(args) else arg[len(option) :]
                absolute = _absolute(command["directory"], value)
                mirrored_dirs.append(absolute)
                rewritten += [option, moved(absolute)]
                i += 2 if arg == option else 1
                continue
            if arg == "-o" and i + 1 < len(args):
                rewritten += [arg, args[i + 1]]
                i += 2
                continue
            if not arg.startswith("-") and arg.endswith(SOURCE_SUFFIXES):
                arg = moved(_absolute(command["directory"], arg))
            rewritten.append(arg)
            i += 1
        extra: list[str] = ["-iquote", mirrored_dirs[0]]
        for directory in dict.fromkeys(mirrored_dirs[1:]):
            extra += ["-idirafter", directory]
        found.append(
            {
                "directory": moved(command["directory"]),
                "arguments": rewritten + extra,
                "file": moved(command["file"]),
            }
        )
    return found


def worktree_exists(worktree: Path) -> Callable[[str], bool]:
    """``exists`` for :func:`for_worktree`: whether the worktree has a relative path."""
    return lambda rel: (worktree / rel).is_file()
