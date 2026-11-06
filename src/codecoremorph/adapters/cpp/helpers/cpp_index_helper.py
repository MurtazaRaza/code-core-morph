# libclang's enum members (CursorKind.CLASS_DECL, ...) are created at import time.
# pyright: reportAttributeAccessIssue=false
"""CodeCoreMorph C++ index helper (SPEC §7.1, §7.2, §11.1). Runs inside the sandbox.

Usage: python -I cpp_index_helper.py REQUEST.json
       python -I cpp_index_helper.py --c-declarations REQUEST.json   (a C header's functions)

Reads compile_commands.json, parses every translation unit with libclang in a process pool, and
writes the index as JSON lines to the request's ``output`` file. It needs only the standard
library and ``clang.cindex`` (the ``libclang`` wheel), because it runs in the toolchain image as
well as on the host.

The request (JSON): workspace, compile_commands, output, cache_dir, exclude (globs relative to
the workspace), files (main files to restrict to, or null) and jobs.

libclang has no headers of its own, so system include directories come from asking each
compiler (``-E -v``); the helper then parses with exactly the compiler's header search path.
Results are cached per translation unit, keyed by its flags and validated against the hashes
of every project file it included (FR-2.4).
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import re
import shlex
import subprocess
import sys
import time

# 2: extern "C" blocks no longer show in qualified names (D-58); 3: a C `static` is on no
# boundary (D-69); 4: nor is a program's `main`; 5: a variadic function's signature ends
# with `...` (D-70)
PROTOCOL = 5
SOURCE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".c++", ".cp")
MAX_TU_PROBLEMS = 3

# Flags that change how code parses. Everything else (-o, -c, -W*, -O*, -g, -M*) is dropped:
# libclang only needs to see the same code, and unknown flags would make it fail.
_TWO_ARG = {
    "-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-D", "-U",
    "-isysroot", "--sysroot", "-x", "-target", "-iprefix", "-iwithprefix",
}  # fmt: skip
_PATH_ARG = {"-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-isysroot",
             "--sysroot"}  # fmt: skip
_JOINED = ("-I", "-D", "-U", "-isystem", "-iquote", "-idirafter", "-std=", "--std=",
           "--target=", "-stdlib=", "--sysroot=", "-isysroot")  # fmt: skip
_KEEP = {
    "-fms-extensions", "-fms-compatibility", "-fno-exceptions", "-fexceptions", "-fno-rtti",
    "-frtti", "-fchar8_t", "-fno-char8_t", "-fdeclspec", "-m32", "-m64", "-pthread",
    "-fsigned-char", "-funsigned-char", "-fshort-wchar", "-ffreestanding", "-fopenmp",
    "-nostdinc", "-nostdinc++", "-fno-operator-names", "-fcoroutines",
}  # fmt: skip
_QUERY_FLAGS = ("-std=", "--std=", "--target=", "-stdlib=", "--sysroot=", "-isysroot", "-m32",
                "-m64", "-nostdinc", "-nostdinc++", "-target")  # fmt: skip


# --------------------------------------------------------------------------- globs


def glob_regex(pattern):
    """The same gitignore-style semantics as codecoremorph.core.globs."""
    text = pattern.strip().replace("\\", "/")
    anchored = "/" in text.rstrip("/")
    text = text.strip("/")
    if not anchored:
        text = "**/" + text
    out = []
    i = 0
    while i < len(text):
        if text.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif text.startswith("**", i):
            out.append(".*")
            i += 2
        elif text[i] == "*":
            out.append("[^/]*")
            i += 1
        elif text[i] == "?":
            out.append("[^/]")
            i += 1
        elif text[i] == "[" and text.find("]", i + 2) != -1:
            end = text.find("]", i + 2)
            body = text[i + 1 : end]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = end + 1
        else:
            out.append(re.escape(text[i]))
            i += 1
    return re.compile("".join(out))


def glob_matches(regexes, rel):
    parts = rel.strip("/").split("/")
    for depth in range(1, len(parts) + 1):
        prefix = "/".join(parts[:depth])
        if any(rx.fullmatch(prefix) for rx in regexes):
            return True
    return False


# --------------------------------------------------------------------------- paths


class Workspace:
    def __init__(self, root, exclude):
        self.case_insensitive = os.name == "nt" or sys.platform == "darwin"
        self.root = self._norm(root).rstrip("/") + "/"
        self._key_root = self._key(self.root)
        self._exclude = [glob_regex(p) for p in exclude if p.strip()]
        self._rel_cache = {}

    def _norm(self, path):
        return os.path.normpath(path).replace("\\", "/")

    def _key(self, path):
        return path.casefold() if self.case_insensitive else path

    def rel(self, path):
        """Workspace-relative POSIX path, or None outside the workspace."""
        cached = self._rel_cache.get(path)
        if cached is not None or path in self._rel_cache:
            return cached
        norm = self._norm(os.path.abspath(path))
        result = None
        if self._key(norm).startswith(self._key_root):
            result = norm[len(self.root) :]
        self._rel_cache[path] = result
        return result

    def excluded(self, rel):
        return bool(self._exclude) and glob_matches(self._exclude, rel)


# --------------------------------------------------------------------------- compile commands


def split_windows(command):
    """Split a command line with the MSVC runtime's rules (CommandLineToArgvW)."""
    args = []
    current = []
    in_quotes = False
    have_arg = False
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            j = i
            while j < n and command[j] == "\\":
                j += 1
            count = j - i
            if j < n and command[j] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    i = j + 1
                else:
                    i = j
                have_arg = True
                continue
            current.append("\\" * count)
            i = j
            have_arg = True
            continue
        if c == '"':
            in_quotes = not in_quotes
            have_arg = True
        elif c in " \t" and not in_quotes:
            if have_arg:
                args.append("".join(current))
                current = []
                have_arg = False
        else:
            current.append(c)
            have_arg = True
        i += 1
    if have_arg:
        args.append("".join(current))
    return args


def entry_argv(entry):
    if "arguments" in entry:
        return list(entry["arguments"])
    command = entry.get("command", "")
    return split_windows(command) if os.name == "nt" else shlex.split(command)


def parse_flags(argv, directory):
    """(libclang args without system includes, flags for the compiler query, language)."""
    out = []
    query = []
    language = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in _TWO_ARG and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            if arg in _PATH_ARG:
                value = os.path.normpath(os.path.join(directory, value))
            if arg == "-x":
                language = value
            out += [arg, value]
            if arg in ("-target", "-isysroot", "--sysroot"):
                query += [arg, value]
            continue
        i += 1
        if arg.startswith(_JOINED):
            for prefix in ("-I", "-isystem", "-iquote", "-idirafter"):
                if (
                    arg.startswith(prefix)
                    and len(arg) > len(prefix)
                    and not arg.startswith("-isys")
                ):
                    path = arg[len(prefix) :]
                    arg = prefix + os.path.normpath(os.path.join(directory, path))
                    break
            out.append(arg)
            if arg.startswith(_QUERY_FLAGS):
                query.append(arg)
        elif arg in _KEEP:
            out.append(arg)
            if arg in _QUERY_FLAGS:
                query.append(arg)
    return out, query, language


_SYSTEM_INCLUDES = {}


def system_include_args(compiler, language, query):
    """``-nostdinc`` plus the compiler's own header search path, asked with ``-E -v``."""
    key = (compiler, language, tuple(query))
    if key in _SYSTEM_INCLUDES:
        return _SYSTEM_INCLUDES[key]
    result = []
    try:
        proc = subprocess.run(
            [compiler, "-x", language, *query, "-E", "-v", "-"],
            input=b"",
            capture_output=True,
            timeout=60,
            check=False,
        )
        text = proc.stderr.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        text = ""
    dirs = []
    target = None
    inside = False
    for line in text.splitlines():
        if line.startswith("Target:"):
            target = line.split(":", 1)[1].strip()
        elif line.startswith("#include <...> search starts here:"):
            inside = True
        elif line.startswith("End of search list."):
            inside = False
        elif inside and line.startswith(" "):
            directory = line.strip()
            if directory.endswith("(framework directory)"):
                directory = directory[: -len("(framework directory)")].strip()
                dirs.append(("-iframework", directory))
            else:
                dirs.append(("-isystem", directory))
    if dirs:
        result = ["-nostdinc", "-nostdinc++"]
        if target and not any(q.startswith(("--target", "-target")) for q in query):
            result.append("--target=" + target)
        for flag, directory in dirs:
            result += [flag, os.path.normpath(directory)]
    _SYSTEM_INCLUDES[key] = result
    return result


def make_jobs(request, workspace):
    with open(request["compile_commands"], encoding="utf-8") as handle:
        entries = json.load(handle)
    only = None
    if request.get("files") is not None:
        only = {workspace._key(os.path.normpath(f).replace("\\", "/")) for f in request["files"]}
    jobs = []
    seen = set()
    problems = []
    for entry in entries:
        directory = entry.get("directory", ".")
        path = os.path.normpath(os.path.join(directory, entry["file"]))
        rel = workspace.rel(path)
        if rel is None or workspace.excluded(rel) or rel in seen:
            continue
        if only is not None and workspace._key(path.replace("\\", "/")) not in only:
            continue
        seen.add(rel)
        argv = entry_argv(entry)
        if not argv:
            continue
        compiler = argv[0]
        if os.path.basename(compiler).lower().startswith("clang-cl") or compiler.lower().endswith(
            ("cl.exe", "cl")
        ):
            problems.append(f"{rel}: MSVC-style compile commands are not indexed")
            continue
        flags, query, language = parse_flags(argv, directory)
        if language is None:
            language = "c" if rel.endswith(".c") else "c++"
        system = system_include_args(compiler, language, query)
        args = ["-x", language, *[a for a in flags if a not in ("-x", language)], *system]
        jobs.append(
            {
                "path": path,
                "rel": rel,
                "lang": "c" if language == "c" else "cpp",
                "args": args,
            }
        )
    return jobs, problems


# --------------------------------------------------------------------------- hashing and cache


_HASHES = {}


def file_sha256(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (path, stat.st_mtime_ns, stat.st_size)
    cached = _HASHES.get(key)
    if cached is None:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        cached = _HASHES[key] = digest.hexdigest()
    return cached


def cache_key(job, libclang_version):
    blob = json.dumps([PROTOCOL, libclang_version, job["rel"], job["lang"], job["args"]])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_load(cache_dir, key):
    path = os.path.join(cache_dir, key[:2], key + ".json")
    try:
        with open(path, encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, ValueError):
        return None
    for dep_path, sha in entry.get("deps", []):
        if file_sha256(dep_path) != sha:
            return None
    return entry


def cache_store(cache_dir, key, entry):
    directory = os.path.join(cache_dir, key[:2])
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, key + ".json")
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(entry, handle, separators=(",", ":"))
    os.replace(tmp, path)


# --------------------------------------------------------------------------- traversal


_STATE = {}


def _worker_init(workspace_root, exclude, cache_dir, libclang_version):
    from clang import cindex

    _STATE["cindex"] = cindex
    _STATE["index"] = cindex.Index.create()
    _STATE["workspace"] = Workspace(workspace_root, exclude)
    _STATE["cache_dir"] = cache_dir
    _STATE["version"] = libclang_version
    _STATE["done_headers"] = set()
    kinds = cindex.CursorKind
    names = {
        "NAMESPACE": "namespace", "CLASS_DECL": "class", "STRUCT_DECL": "struct",
        "UNION_DECL": "union", "ENUM_DECL": "enum", "ENUM_CONSTANT_DECL": "enum_constant",
        "FUNCTION_DECL": "function", "CXX_METHOD": "method", "CONVERSION_FUNCTION": "method",
        "CONSTRUCTOR": "constructor", "DESTRUCTOR": "destructor", "FIELD_DECL": "field",
        "VAR_DECL": "variable", "TYPEDEF_DECL": "typedef", "TYPE_ALIAS_DECL": "alias",
        "CLASS_TEMPLATE": "class_template",
        "CLASS_TEMPLATE_PARTIAL_SPECIALIZATION": "class_template",
        "FUNCTION_TEMPLATE": "function_template", "TYPE_ALIAS_TEMPLATE_DECL": "alias_template",
        "CONCEPT_DECL": "concept", "MACRO_DEFINITION": "macro",
    }  # fmt: skip
    _STATE["decl_kinds"] = {
        getattr(kinds, name): value for name, value in names.items() if hasattr(kinds, name)
    }
    refs = {
        "CALL_EXPR": "call", "TYPE_REF": "type_use", "TEMPLATE_REF": "type_use",
        "CXX_BASE_SPECIFIER": "inherit", "MACRO_INSTANTIATION": "macro_expand",
        "DECL_REF_EXPR": "use", "MEMBER_REF_EXPR": "use", "MEMBER_REF": "use",
    }  # fmt: skip
    _STATE["ref_kinds"] = {getattr(kinds, n): v for n, v in refs.items() if hasattr(kinds, n)}
    function_like = ("FUNCTION_DECL", "CXX_METHOD", "CONSTRUCTOR", "DESTRUCTOR",
                     "CONVERSION_FUNCTION", "FUNCTION_TEMPLATE", "LAMBDA_EXPR")  # fmt: skip
    _STATE["function_like"] = {getattr(kinds, n) for n in function_like if hasattr(kinds, n)}
    records = ("CLASS_DECL", "STRUCT_DECL", "UNION_DECL", "CLASS_TEMPLATE",
               "CLASS_TEMPLATE_PARTIAL_SPECIALIZATION")  # fmt: skip
    _STATE["record_kinds"] = {getattr(kinds, n) for n in records if hasattr(kinds, n)}
    _STATE["linkage_spec"] = getattr(kinds, "LINKAGE_SPEC", None)
    linkage = getattr(cindex, "LinkageKind", None)
    _STATE["internal_linkage"] = getattr(linkage, "INTERNAL", None) if linkage else None
    _STATE["unexposed_decl"] = kinds.UNEXPOSED_DECL
    access = cindex.AccessSpecifier
    _STATE["access"] = {
        access.PUBLIC: "public",
        access.PROTECTED: "protected",
        access.PRIVATE: "private",
    }


def _internal(cursor):
    """Whether the symbol has internal linkage (a C `static`): it is no C interface."""
    internal = _STATE.get("internal_linkage")
    if internal is None:
        return False
    try:
        return cursor.linkage == internal
    except Exception:
        return False


def _is_extern_c(cursor):
    kind = cursor.kind
    if kind == _STATE["linkage_spec"]:
        tokens = [t.spelling for t in cursor.get_tokens()][:2]
        return tokens[:1] == ["extern"] and (len(tokens) < 2 or tokens[1] == '"C"')
    if kind == _STATE["unexposed_decl"]:
        tokens = [t.spelling for t in cursor.get_tokens()][:2]
        return tokens == ["extern", '"C"']
    return False


def _qualified(cursor, cache):
    parts = []
    parent = cursor.semantic_parent
    prefix = ""
    while parent is not None:
        key = parent.hash
        if key in cache:
            prefix = cache[key]
            break
        if parent.kind.is_translation_unit():
            break
        # An extern "C" block is a declaration context without a name: it adds nothing to the
        # names of what it declares.
        if parent.kind != _STATE["linkage_spec"]:
            parts.append(parent)
        parent = parent.semantic_parent
    for node in reversed(parts):
        name = node.spelling or "(anonymous)"
        prefix = f"{prefix}::{name}" if prefix else name
        cache[node.hash] = prefix
    name = cursor.spelling or "(anonymous)"
    return f"{prefix}::{name}" if prefix else name


def _variadic(cursor):
    """Whether a function takes ``...``: its arguments don't show it."""
    try:
        kind = cursor.type.kind
        return kind.name == "FUNCTIONPROTO" and cursor.type.is_function_variadic()
    except Exception:
        return False


def _signature(cursor, kind_name, qualified):
    try:
        if kind_name in ("function", "method", "constructor", "destructor", "function_template"):
            args = ", ".join(a.type.spelling for a in cursor.get_arguments())
            if _variadic(cursor):
                args = f"{args}, ..." if args else "..."
            result = cursor.result_type.spelling
            text = f"{result} {qualified}({args})".strip()
            if kind_name == "method" and cursor.is_const_method():
                text += " const"
            return text
        if kind_name in ("typedef", "alias"):
            return f"{qualified} = {cursor.underlying_typedef_type.spelling}"
        if kind_name in ("field", "variable"):
            return f"{cursor.type.spelling} {qualified}"
    except Exception:  # libclang can't describe every construct; the signature is optional
        return None
    return None


def index_tu(job):
    cindex = _STATE["cindex"]
    workspace = _STATE["workspace"]
    started = time.monotonic()
    key = cache_key(job, _STATE["version"])
    cached = cache_load(_STATE["cache_dir"], key)
    if cached is not None:
        return {
            "rel": job["rel"],
            "cached": True,
            "records": cached["records"],
            "problems": cached.get("problems", []),
            "seconds": time.monotonic() - started,
        }
    try:
        tu = _STATE["index"].parse(
            job["path"],
            args=job["args"],
            options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )
    except cindex.TranslationUnitLoadError as exc:
        return {
            "rel": job["rel"],
            "cached": False,
            "records": [],
            "failed": True,
            "problems": [f"{job['rel']}: libclang could not parse it ({exc})"],
            "seconds": time.monotonic() - started,
        }

    records = []
    deps = {}
    files = {job["rel"]}
    for inclusion in tu.get_includes():
        source = inclusion.source.name if inclusion.source else None
        target = inclusion.include.name if inclusion.include else None
        if target is None:
            continue
        target_rel = workspace.rel(target)
        if target_rel is None:
            continue
        deps[os.path.abspath(target)] = None
        files.add(target_rel)
        source_rel = workspace.rel(source) if source else None
        if source_rel is not None:
            records.append(["dep", source_rel, target_rel])
    is_c = job["lang"] == "c"
    for rel in sorted(files):
        records.append(["file", rel, is_c])

    problems = []
    for diag in tu.diagnostics:
        if diag.severity >= cindex.Diagnostic.Error and len(problems) < MAX_TU_PROBLEMS:
            location = diag.location
            where = workspace.rel(location.file.name) if location.file else None
            if where is not None:
                problems.append(f"{where}:{location.line}:{location.column}: {diag.spelling}")

    skip_headers = set(_STATE["done_headers"])
    traversed = set()
    collector = _Collector(job, workspace, skip_headers, traversed)
    collector.walk(tu.cursor, None, is_c)
    records.extend(collector.records)
    _STATE["done_headers"].update(h for h in traversed if h != job["rel"])

    entry = {
        "deps": [[path, file_sha256(path)] for path in sorted(deps)]
        + [[os.path.abspath(job["path"]), file_sha256(job["path"])]],
        "records": records,
        "problems": problems,
    }
    cache_store(_STATE["cache_dir"], key, entry)
    return {
        "rel": job["rel"],
        "cached": False,
        "records": records,
        "problems": problems,
        "seconds": time.monotonic() - started,
    }


class _Collector:
    def __init__(self, job, workspace, skip_headers, traversed):
        self.job = job
        self.workspace = workspace
        self.skip = skip_headers
        self.traversed = traversed
        self.records = []
        self.refs = {}
        self.qualified_cache = {}
        self.decl_kinds = _STATE["decl_kinds"]
        self.ref_kinds = _STATE["ref_kinds"]
        self.function_like = _STATE["function_like"]
        self.access = _STATE["access"]
        self.record_kinds = _STATE["record_kinds"]

    def walk(self, cursor, enclosing, extern_c):
        for child in cursor.get_children():
            location = child.location
            file = location.file
            if file is None:
                continue
            rel = self.workspace.rel(file.name)
            if rel is None or self.workspace.excluded(rel):
                continue
            if rel in self.skip and rel != self.job["rel"]:
                continue
            self.traversed.add(rel)
            child_extern_c = extern_c or _is_extern_c(child)
            kind = child.kind
            symbol = enclosing
            kind_name = self.decl_kinds.get(kind)
            if kind_name is not None:
                usr = child.get_usr()
                if usr and not self._is_local(child, kind_name):
                    self._symbol(child, kind_name, usr, rel, location, child_extern_c)
                    symbol = usr
            ref_kind = self.ref_kinds.get(kind)
            if ref_kind is not None:
                self._reference(child, ref_kind, rel, location, enclosing)
            self.walk(child, symbol, child_extern_c)
        if cursor.kind.is_translation_unit():
            for (rel, line, column, target), (ref_kind, source) in self.refs.items():
                self.records.append(["ref", rel, source, target, ref_kind, line, column])

    def _is_local(self, cursor, kind_name):
        if kind_name not in (
            "variable",
            "typedef",
            "alias",
            "class",
            "struct",
            "union",
            "enum",
            "enum_constant",
            "function",
        ):
            return False
        parent = cursor.semantic_parent
        return parent is not None and parent.kind in self.function_like

    def _symbol(self, cursor, kind_name, usr, rel, location, extern_c):
        qualified = _qualified(cursor, self.qualified_cache)
        extent = cursor.extent
        start, end = extent.start, extent.end
        if start.file is None or end.file is None or start.file.name != end.file.name:
            start_line, start_col, end_line, end_col = (
                location.line, location.column, location.line, location.column,
            )  # fmt: skip
        else:
            start_line, start_col, end_line, end_col = (
                start.line,
                start.column,
                end.line,
                end.column,
            )
        boundary = "none"
        if (
            kind_name in ("function", "variable")
            and (extern_c or self.job["lang"] == "c")
            and not _internal(cursor)
            and not (kind_name == "function" and cursor.spelling == "main")
        ):
            boundary = "c_abi"
        try:
            is_definition = bool(cursor.is_definition())
        except Exception:
            is_definition = False
        visibility = "none"
        parent = cursor.semantic_parent
        if parent is not None and parent.kind in self.record_kinds:
            visibility = self.access.get(cursor.access_specifier, "none")
        self.records.append([
            "sym", usr, cursor.spelling or "(anonymous)", qualified, kind_name, rel,
            max(start_line, 1), max(start_col, 1), max(end_line, 1), max(end_col, 1),
            _signature(cursor, kind_name, qualified), visibility, is_definition, boundary,
        ])  # fmt: skip

    def _reference(self, cursor, ref_kind, rel, location, enclosing):
        try:
            target = cursor.referenced
        except Exception:
            return
        if target is None:
            return
        usr = target.get_usr()
        if not usr:
            return
        key = (rel, max(location.line, 1), max(location.column, 1), usr)
        existing = self.refs.get(key)
        if existing is None or (existing[0] == "use" and ref_kind == "call"):
            self.refs[key] = (ref_kind, enclosing)


# --------------------------------------------------------------------------- main


def libclang_version():
    from clang import cindex

    # cindex doesn't declare this function. It returns a CXString struct by value: without the
    # right restype, ctypes corrupts memory on Windows x64.
    function = cindex.conf.lib.clang_getClangVersion
    function.restype = cindex._CXString
    function.errcheck = cindex._CXString.from_result
    return function()


def main(argv):
    if len(argv) != 2:
        print("usage: cpp_index_helper.py REQUEST.json", file=sys.stderr)
        return 2
    started = time.monotonic()
    with open(argv[1], encoding="utf-8") as handle:
        request = json.load(handle)
    workspace = Workspace(request["workspace"], request.get("exclude", []))
    version = libclang_version()
    jobs, problems = make_jobs(request, workspace)
    cache_dir = request["cache_dir"]
    os.makedirs(cache_dir, exist_ok=True)
    jobs_n = max(1, min(int(request.get("jobs") or os.cpu_count() or 1), len(jobs) or 1))

    counts: dict[str, float] = {
        "translation_units": len(jobs),
        "parsed": 0,
        "cached": 0,
        "failed": 0,
    }
    seen_records = set()
    out_path = request["output"]
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        init = (request["workspace"], request.get("exclude", []), cache_dir, version)
        if jobs_n == 1:
            _worker_init(*init)
            results = map(index_tu, jobs)
            pool = None
        else:
            pool = multiprocessing.Pool(jobs_n, initializer=_worker_init, initargs=init)
            results = pool.imap_unordered(index_tu, jobs, chunksize=1)
        try:
            for result in results:
                if result.get("failed"):
                    counts["failed"] += 1
                elif result["cached"]:
                    counts["cached"] += 1
                else:
                    counts["parsed"] += 1
                problems.extend(result.get("problems", []))
                out.write(
                    json.dumps(["tu", result["rel"], result["cached"], round(result["seconds"], 4)])
                    + "\n"
                )
                for record in result["records"]:
                    line = json.dumps(record, separators=(",", ":"))
                    digest = hash(line)
                    if digest in seen_records:
                        continue
                    seen_records.add(digest)
                    out.write(line + "\n")
        finally:
            if pool is not None:
                pool.close()
                pool.join()
        for problem in problems:
            out.write(json.dumps(["problem", problem]) + "\n")
        if request.get("cmake_build_dir"):
            for target in cmake_targets(request["cmake_build_dir"], workspace):
                out.write(json.dumps(["target", *target]) + "\n")
        counts["seconds"] = round(time.monotonic() - started, 3)
        out.write(
            json.dumps(["meta", {"libclang": version, "protocol": PROTOCOL, **counts}]) + "\n"
        )
    os.replace(tmp, out_path)
    return 0


# --------------------------------------------------------------------------- CMake targets


def cmake_targets(build_dir, workspace):
    """``[name, type, files, dependencies, artifacts]`` for each of the project's own CMake
    targets, from the file API reply to the codemodel query the configure step writes:
    imported targets are left out, files are workspace-relative, dependencies are the names of
    the project's targets it depends on, and artifacts are relative to the build directory.
    Nothing when there is no reply."""
    reply = os.path.join(build_dir, ".cmake", "api", "v1", "reply")
    try:
        indexes = sorted(
            n for n in os.listdir(reply) if n.startswith("index-") and n.endswith(".json")
        )
    except OSError:
        return []

    def load(name):
        with open(os.path.join(reply, name), encoding="utf-8") as handle:
            return json.load(handle)

    try:
        codemodel = load(load(indexes[-1])["reply"]["codemodel-v2"]["jsonFile"])
    except (IndexError, KeyError, OSError, TypeError, ValueError):
        return []
    source_dir = codemodel.get("paths", {}).get("source", "")
    found = []
    for configuration in codemodel.get("configurations", [])[:1]:
        loaded = []
        names = {}
        for ref in configuration.get("targets", []):
            try:
                target = load(ref["jsonFile"])
            except (KeyError, OSError, TypeError, ValueError):
                continue
            name = target.get("name") or ref.get("name", "")
            names[target.get("id") or ref.get("id", "")] = name
            if not target.get("imported"):
                loaded.append((name, target))
        for name, target in loaded:
            files = []
            for source in target.get("sources", []):
                path = source.get("path", "")
                rel = workspace.rel(path if os.path.isabs(path) else os.path.join(source_dir, path))
                if rel is not None and rel not in files:
                    files.append(rel)
            dependencies = sorted(
                {names[d["id"]] for d in target.get("dependencies", []) if d.get("id") in names}
            )
            artifacts = sorted(
                a["path"].replace(os.sep, "/")
                for a in target.get("artifacts", [])
                if isinstance(a.get("path"), str)
            )
            found.append([name, target.get("type", ""), sorted(files), dependencies, artifacts])
    return sorted(found)


# --------------------------------------------------------------------------- C declarations


_C_FLAGS = ("-I", "-isystem", "-iquote", "-idirafter", "-D", "-U", "--target=", "-target",
            "--sysroot", "-isysroot", "-m32", "-m64")  # fmt: skip


def c_flags(compile_commands):
    """The compiler and the flags that find headers (include directories, macros, target)
    over every compile command, for parsing a header as C on its own."""
    try:
        with open(compile_commands, encoding="utf-8") as handle:
            entries = json.load(handle)
    except OSError:
        return None, [], []
    compiler = None
    flags = []
    query = []
    for entry in entries:
        argv = entry_argv(entry)
        if not argv:
            continue
        compiler = compiler or argv[0]
        out, found_query, _ = parse_flags(argv, entry.get("directory", "."))
        i = 0
        while i < len(out):
            arg = out[i]
            group = [arg]
            if arg in _TWO_ARG and i + 1 < len(out):
                group.append(out[i + 1])
            i += len(group)
            if arg.startswith(_C_FLAGS) and not _contains(flags, group):
                flags += group
        for arg in found_query:
            if not arg.startswith(("-std=", "--std=", "-stdlib=")) and arg not in query:
                query.append(arg)
    return compiler, flags, query


def _contains(flags, group):
    n = len(group)
    return any(flags[i : i + n] == group for i in range(len(flags) - n + 1))


def c_declarations(request):
    """``{"functions": {name: canonical signature}, "problems": [...]}`` for the functions a C
    header declares (not those it defines), parsed as C11 with the project's include flags."""
    from clang import cindex

    header = os.path.normpath(request["header"])
    compiler, flags, query = c_flags(request["compile_commands"])
    args = ["-x", "c", "-std=c11", *flags, "-I", os.path.dirname(header)]
    if compiler:
        args += system_include_args(compiler, "c", query)
    tu = cindex.Index.create().parse(header, args=args)
    problems = [
        f"{d.location.file.name if d.location.file else header}:{d.location.line}: {d.spelling}"
        for d in tu.diagnostics
        if d.severity >= cindex.Diagnostic.Error
    ][:MAX_TU_PROBLEMS]
    key = os.path.normcase(os.path.abspath(header))
    functions = {}
    for cursor in tu.cursor.get_children():
        if cursor.kind != cindex.CursorKind.FUNCTION_DECL or cursor.location.file is None:
            continue
        if os.path.normcase(os.path.abspath(cursor.location.file.name)) != key:
            continue
        if cursor.is_definition() or cursor.storage_class == cindex.StorageClass.STATIC:
            continue
        functions[cursor.spelling] = cursor.type.get_canonical().spelling
    return {"functions": dict(sorted(functions.items())), "problems": problems}


def main_c_declarations(argv):
    with open(argv[2], encoding="utf-8") as handle:
        request = json.load(handle)
    result = c_declarations(request)
    tmp = request["output"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        json.dump(result, out, indent=1, sort_keys=True)
    os.replace(tmp, request["output"])
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--c-declarations":
        sys.exit(main_c_declarations(sys.argv))
    sys.exit(main(sys.argv))
