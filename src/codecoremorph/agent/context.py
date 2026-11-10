"""The prompt for a unit: a stable system prompt and the unit's first message (SPEC §7.6).

- System prompt: role instructions, edit format and constraints (one template, the same for
  every unit of a migration), then the guidance of the unit's rules. Both blocks are byte-
  stable, so they cache across units with the same rules.
- First message: the unit's rules, the full text of its files after codemods, signatures of
  the out-of-unit symbols they use, the codemod summary, the remaining rule sites and the
  current build state. It ends with a cache breakpoint: every later turn reuses it.

Files are never truncated: a context over the model's budget is an error (FR-5.4).

A host agent (D-41) gets the same content as one Markdown document (``host_document``): the
host instructions, the rules and the unit message.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from codecoremorph.core.errors import ContextOverflowError
from codecoremorph.core.models import (
    Boundary,
    CodemodResult,
    LoadedRule,
    Message,
    PortUnit,
    PortUnits,
    RuleKind,
    RuleSite,
    SystemBlock,
    ToolSpec,
    WorkUnit,
    owned_files,
)
from codecoremorph.patching.textfile import BinaryFileError, TextFile
from codecoremorph.planner.estimate import estimate_tokens
from codecoremorph.prompts import render
from codecoremorph.store.index import IndexStore, takes_variable_arguments

MAX_SIGNATURES = 80
MAX_UNCOMPILED = 40
MAX_SITES_SHOWN = 40
_FENCE_LANGUAGES = {
    ".h": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".inl": "cpp", ".ipp": "cpp",
    ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".txt": "cmake", ".cmake": "cmake",
    ".java": "java", ".cs": "csharp", ".rs": "rust",
}  # fmt: skip


@dataclass(frozen=True)
class UnitContext:
    system: list[SystemBlock]
    first_message: Message
    tokens: int


def read_text(worktree: Path, path: str) -> str | None:
    try:
        return TextFile.decode((worktree / path).read_bytes()).text.replace("\r\n", "\n")
    except (OSError, BinaryFileError):
        return None


def fenced(path: str, text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    suffix = Path(path).suffix.lower()
    language = _FENCE_LANGUAGES.get(suffix, "")
    body = text if text.endswith("\n") else text + "\n"
    return f"### {path}\n\n{fence}{language}\n{body}{fence}"


def rules_section(rules: Sequence[LoadedRule], *, nest_guidance: int = 0) -> str:
    """Each rule's description and guidance, under one heading. ``nest_guidance`` pushes the
    guidance's own headings that many levels down (the system prompt keeps them as written,
    so its cached bytes don't change)."""
    sections: list[str] = ["# Rules of this unit"]
    for loaded in rules:
        rule = loaded.rule
        part = f"## {rule.id} ({rule.kind.value})\n\n{rule.description.strip()}"
        guidance = loaded.guidance_path()
        if guidance is not None and guidance.is_file():
            text = guidance.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
            part += "\n\n" + _demote(text, nest_guidance)
        sections.append(part)
    return "\n\n".join(sections)


def system_prompt(
    rules: Sequence[LoadedRule], *, language: str, source: str, target: str
) -> list[SystemBlock]:
    base = render("editor_system", language=language, source=source, target=target)
    return [
        SystemBlock(text=base.strip()),
        SystemBlock(text=rules_section(rules), cache_breakpoint=True),
    ]


def outside_signatures(index_path: Path | None, files: Sequence[str]) -> list[str]:
    """Declarations of the symbols the unit's files use from other files."""
    if index_path is None or not index_path.is_file():
        return []
    unit = set(files)
    targets: set[str] = set()
    with IndexStore.open(index_path) as store:
        for path in files:
            targets.update(r.to_symbol for r in store.references_from(path))
        lines: list[str] = []
        for symbol_id in sorted(targets):
            symbols = store.symbols(symbol_id=symbol_id)
            if not symbols or any(s.file in unit for s in symbols):
                continue
            # Prefer a declaration (usually in a header) over the definition.
            chosen = min(symbols, key=lambda s: (s.is_definition, s.file, s.range.start_line))
            text = chosen.signature or chosen.qualified_name
            lines.append(
                f"- {chosen.qualified_name}: `{text}` ({chosen.file}:{chosen.range.start_line})"
            )
    return sorted(set(lines))


def codemod_summary(result: CodemodResult | None) -> str:
    if result is None:
        return "No deterministic tools ran for these rules."
    if not result.changes:
        text = "The deterministic tools ran and changed nothing."
    else:
        total = sum(c.replacements for c in result.changes)
        listed = ", ".join(f"{c.file} ({c.replacements})" for c in result.changes)
        text = (
            f"The deterministic tools made {total} replacements: {listed}. "
            "The files above include them."
        )
    if result.skipped:
        text += "\n\nFixes they left out:\n" + "\n".join(
            f"- {s.file} ({s.check}): {s.reason}" for s in result.skipped[:20]
        )
    return text


def remaining_summary(sites: Mapping[str, Sequence[RuleSite]]) -> str:
    parts: list[str] = []
    for rule_id, rule_sites in sites.items():
        if not rule_sites:
            continue
        lines = [f"{rule_id}: {len(rule_sites)} site(s)"]
        lines += [f"- {s.file}:{s.line}: {s.text}" for s in rule_sites[:MAX_SITES_SHOWN]]
        if len(rule_sites) > MAX_SITES_SHOWN:
            lines.append(f"[{len(rule_sites) - MAX_SITES_SHOWN} more]")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) or "The rules' detectors find no sites left; see the build below."


def unit_message(
    unit: WorkUnit,
    *,
    worktree: Path,
    index_path: Path | None,
    codemods: CodemodResult | None,
    remaining: Mapping[str, Sequence[RuleSite]],
    build: str | None,
    find_symbol: str = "find_symbol",
    port: bool = False,
    port_units: PortUnits | None = None,
    c_port: bool = False,
    rust_port: bool = False,
    csharp_target: str | None = None,
) -> str:
    """The unit's first message: its files in full, signatures, codemods, sites, build.

    A ``port`` unit (D-56) also shows the files its codemods created (the new code's
    scaffold), and its reference files are the headers it keeps as they are. A port from C#
    names its target language in ``csharp_target`` (D-78)."""
    blocks: list[str] = []
    created = [
        f for f in (codemods.changed_files if codemods and port else []) if f not in unit.files
    ]
    for path in [*unit.files, *created]:
        text = read_text(worktree, path)
        if text is None and path in created:
            continue  # removed again since
        blocks.append(
            fenced(path, text) if text is not None else f"### {path}\n\n(not a text file)"
        )
    signatures = outside_signatures(index_path, unit.files)
    if len(signatures) > MAX_SIGNATURES:
        shown = signatures[:MAX_SIGNATURES]
        shown.append(f"[{len(signatures) - MAX_SIGNATURES} more; use {find_symbol}]")
        signatures = shown
    note = f"{unit.note}\n\n" if unit.note else ""
    if rust_port:
        note += rust_port_note(unit, port_units)
    elif csharp_target is not None:
        note += csharp_port_note(unit, port_units, csharp_target)
    elif port_units is PortUnits.RETIREMENT:
        note += c_retirement_note(unit) if c_port else retirement_note(unit)
    elif unit.port is not None and port_units is PortUnits.EXECUTABLE and c_port:
        note += c_executable_note(unit.port)
    elif unit.port is not None and port_units is PortUnits.EXECUTABLE:
        note += executable_note(unit, unit.port)
    elif unit.port is not None and port_units is PortUnits.TESTS:
        note += c_tests_note(unit, unit.port) if c_port else tests_note(unit, unit.port)
    elif unit.port is not None and port_units is PortUnits.TRANSLATION_UNIT:
        note += translation_unit_note(unit, unit.port, c_variadic(index_path, unit.files))
    elif unit.port is not None:
        note += module_port_note(unit.port)
    elif port and unit.reference_files:
        note += (
            f"Kept as they are: {', '.join(unit.reference_files)}. They declare the interface "
            "the new code must export, with the same signatures; you may not edit them.\n\n"
        )
    elif unit.reference_files:
        note += (
            "In this unit only for their references to the symbols it changes: "
            f"{', '.join(unit.reference_files)}. Update their uses as the change needs; their "
            "own rule sites are other units' work.\n\n"
        )
    return render(
        "unit_message",
        unit_id=unit.id,
        rules=", ".join(unit.rules),
        note=note,
        files="\n\n".join(blocks),
        signatures="\n".join(signatures) or "None found in the index.",
        codemods=codemod_summary(codemods),
        remaining=remaining_summary(remaining),
        build=build or "The project builds and its tests pass.",
    )


def module_port_note(port: PortUnit) -> str:
    """Where a module port's code goes, and what stays (D-58)."""
    crate, module = port.crate, port.module
    callers = ", ".join(port.callers) or "none"
    kept_c = (
        f" {', '.join(port.c_headers)} declare a C interface: export those functions from Rust "
        'with the same signatures (#[no_mangle] extern "C") instead of keeping C++ for them.'
        if port.c_headers
        else ""
    )
    return (
        f"Port the module's logic to the Rust module `{crate}::{module}` "
        f"(rust/{crate}/src/{module}.rs, safe Rust), with its C interface for the façade in "
        f"rust/{crate}/src/bridge/{module}.rs, the one place unsafe code may go. The module's "
        "headers keep their public API (private members may change), and its sources become "
        f"a façade: they forward to Rust through {crate}/bridge.h (cbindgen writes it from the "
        "crate before each build; don't edit it) and the helpers of "
        f"{crate}/bridge.hpp. These files keep using the module through its headers and "
        f"don't change: {callers}.{kept_c} Add the module's entries to rust/symbol-map.toml "
        "(read it first: it also says where earlier modules went).\n\n"
    )


def c_variadic(index_path: Path | None, files: Sequence[str]) -> list[tuple[str, str]]:
    """``(file, function)`` for the C interface of ``files`` that takes ``...`` or a
    ``va_list``, which stays C in a port (D-70)."""
    if index_path is None or not index_path.is_file():
        return []
    found: list[tuple[str, str]] = []
    with IndexStore.open(index_path) as store:
        for path in files:
            if not path.lower().endswith(".c"):
                continue
            found += [
                (path, s.name)
                for s in store.used_elsewhere(path)
                if s.boundary is Boundary.C_ABI and takes_variable_arguments(s)
            ]
    return found


def translation_unit_note(
    unit: WorkUnit, port: PortUnit, variadic: Sequence[tuple[str, str]] = ()
) -> str:
    """A C source file's unit (D-68): where its code goes, what it exports, what stays."""
    crate, module = port.crate, port.module
    kept = set(unit.reference_files)
    gone = [f for f in unit.files if f not in kept and f.lower().endswith((".c", ".h"))]
    exports = ", ".join(port.exports) or "none"
    callers = ", ".join(port.callers) or "none"
    headers = ", ".join(unit.reference_files) or "none"
    automake = any(f.rsplit("/", 1)[-1] == "Makefile.am" for f in unit.files)
    build = (
        "with its file in its library's `_SOURCES` in the Makefile.am"
        if automake
        else "with its objects in the build files"
    )
    them = "them" if len(gone) > 1 else "it"
    deletion = f", and delete {them} once Rust does the job, {build}."
    if variadic:
        names = " and ".join(f"`{name}`" for _, name in variadic)
        files = ", ".join(sorted({f for f, _ in variadic}))
        deletion = (
            f". {names} stay in {files}: stable Rust can't define a function that takes `...` "
            "or a `va_list`. Reduce them to C that formats the arguments and calls a function "
            "the bridge exports for the rest, and delete everything else there once Rust does "
            "the job; the file stays in the build until the retirement deletes it."
        )
    return (
        f"Port {', '.join(gone)} to the Rust module `{crate}::{module}` "
        f"(rust/{crate}/src/{module}.rs, safe Rust){deletion} Export what other files "
        f"use, under the same C names and signatures, from rust/{crate}/src/bridge/{module}.rs, "
        '`#[no_mangle] pub extern "C"` functions and `#[no_mangle]` statics, the one place '
        f"unsafe code may go: {exports}. The headers stay as they are (read-only): {headers}. "
        f"Their types, constants and the functions still in C are in `{crate}::bridge::sys` "
        f"(rust/{crate}/src/bridge/sys.rs, which bindgen writes from them before each build; "
        "don't edit "
        "it, and define no copy of its types). These files keep calling the code through the "
        f"headers and don't change: {callers}. The build folds the crate into the library, so "
        "the C callers and tests link against Rust. Add the module's entries to "
        "rust/symbol-map.toml (read it first: it also says where earlier units went).\n\n"
    )


def executable_note(unit: WorkUnit, port: PortUnit) -> str:
    """An executable's unit (D-60): the program it becomes, and its tests."""
    crate = port.crate
    deps = ", ".join(port.dependencies) or "none"
    tests = ", ".join(port.tests)
    translate = (
        f" Translate the CTest entries that run it ({tests}) into integration tests of {crate} "
        f"(rust/{crate}/tests/), and map each in rust/test-map.toml."
        if port.tests
        else ""
    )
    return (
        f"Port the program `{port.bin}` to Rust: rust/{crate}/src/main.rs, the `bin` named "
        f"`{port.bin}`, calling the Rust API of its library crates ({deps}), never their "
        "`bridge` module. Delete its C++ sources (listed under Left to do) once the program "
        f"does their job.{translate}\n\n"
    )


def c_executable_note(port: PortUnit) -> str:
    """A C program's unit (D-68): the program it becomes, where the build puts it, its
    tests."""
    crate = port.crate
    deps = ", ".join(port.dependencies) or "none"
    tests = ", ".join(port.tests)
    translate = (
        f" These tests run it ({tests}): translate those you can into integration tests of "
        f"{crate} (rust/{crate}/tests/), and map each in rust/test-map.toml."
        if port.tests
        else ""
    )
    return (
        f"Port the C program `{port.bin}` to Rust: rust/{crate}/src/main.rs, the `bin` named "
        f"`{port.bin}`, which the Make build copies to {port.artifact or port.bin}. It calls "
        f"the Rust API of its library crates ({deps}), never their `bridge` or `sys` modules. "
        "Delete its C sources (listed under Left to do) once the program does their job, with "
        f"what the build files still say about them.{translate}\n\n"
    )


def tests_note(unit: WorkUnit, port: PortUnit) -> str:
    """A test file's unit (D-60): where its tests go, and what to map."""
    crate, module = port.crate, port.module
    shown = ", ".join(port.tests) or "none"
    wrappers = (
        f" The codemod mapped {', '.join(port.wrappers)} (it only runs the test binary) to its "
        "cases already."
        if port.wrappers
        else ""
    )
    return (
        f"Translate the C++ tests of {', '.join(unit.reference_files)} into "
        f"rust/{crate}/tests/{module}.rs (an integration test of {crate}: its public API only), "
        "one Rust test per case, each checking at least as much as the original, and map each "
        f"in rust/test-map.toml: {shown}.{wrappers} The C++ files stay as they are (read-only) "
        "and keep running.\n\n"
    )


def c_tests_note(unit: WorkUnit, port: PortUnit) -> str:
    """A C test file's unit (D-68): where its tests go, and what to map."""
    crate, module = port.crate, port.module
    shown = ", ".join(port.tests) or "none"
    whole = [t for t in port.tests if t.startswith("make:")]
    programs = (
        f" {', '.join(whole)} {'is a whole program' if len(whole) == 1 else 'are whole programs'}"
        " (automake's test, by its exit status): give its checks Rust tests and map it to all of "
        "them (`rust = [...]`)."
        if whole
        else ""
    )
    return (
        f"Translate the C tests of {', '.join(unit.reference_files)} into "
        f"rust/{crate}/tests/{module}.rs (an integration test of {crate}: its safe Rust API, "
        "never its `bridge` or `sys`), one Rust test per test the C program reports (a TAP "
        "point, an automake test), each checking at least as much as the original, and map "
        f"each in rust/test-map.toml (`c = ...`, `rust = ...`): {shown}.{programs} What a C "
        "test checks "
        "through the C interface (a struct's fields, a NUL terminator, errno) is checked on "
        "the Rust values that stand for it. The C files stay as they are (read-only) and keep "
        "running.\n\n"
    )


def c_retirement_note(unit: WorkUnit) -> str:
    """A C port's retirement (D-68): what went, and what is left to do; with the public API
    kept, what stays (D-72)."""
    if unit.kept_api:
        api = ", ".join(unit.kept_api)
        one = len(unit.kept_api) == 1
        it, they = ("it", "it") if one else ("they", "them")
        return (
            f"The codemod deleted the {len(owned_files(unit))} C files the kept C interface "
            f"doesn't need (keep_source_api). {api} {'stays' if one else 'stay'} exactly as "
            f"{it} {'is' if one else 'are'}, with the headers {it} "
            f"{'includes' if one else 'include'}, the Make build, each crate's bridge with its "
            f"`sys` module, the C that still defines what {it} "
            f"{'declares' if one else 'declare'} and the C tests that use only {they}. Keep "
            "exporting every function the kept headers "
            "declare (c_abi_kept checks every crate), remove the exports and bridge code only a "
            "deleted header needed, take what the deleted files needed out of the Makefile, and "
            "give every baseline test an entry in rust/test-map.toml: a coarse one, such as "
            "`make:test` (the test target's exit status), maps to the Rust tests that took over "
            "what it checked beyond the tests mapped one by one (`rust = [...]`), with a note."
            "\n\n"
        )
    return (
        f"The codemod retired the C: it deleted {len(unit.files)} C and Make files and the "
        "crates' C bridges with their `sys` modules, so the Cargo workspace now builds and "
        "tests alone. Make it build, pass and lint clean on its own (code only the bridge "
        "used, docs that describe the C), and give every baseline test an entry in "
        "rust/test-map.toml: a coarse one, such as `make:test` (the test target's exit "
        "status), maps to the Rust tests that took over what it checked beyond the tests "
        "mapped one by one (`rust = [...]`), with a note.\n\n"
    )


def retirement_note(unit: WorkUnit) -> str:
    """The retirement (D-60): what went, and what is left to do."""
    return (
        f"The codemod retired the C++: it deleted {len(unit.files)} C++ and CMake files and the "
        "crates' C bridges, so the Cargo workspace now builds and tests alone. Make it build, "
        "pass and lint clean on its own (code only the bridge used, docs that describe the "
        "C++), and give every baseline test an entry in rust/test-map.toml.\n\n"
    )


def rust_port_note(unit: WorkUnit, port_units: PortUnits | None) -> str:
    """A port from Rust (to C, D-75): where the unit's C goes, and what stays Rust."""
    port = unit.port
    if port_units is PortUnits.RETIREMENT or port is None:
        return (
            "The codemod retired the Rust: it deleted the Cargo manifests and lock files, the "
            "build script, the link list and every Rust file, so the CMake build now builds and "
            "tests the C alone. Make it build and pass on its own, with every warning and "
            "clang-tidy finding gone, and give every baseline test an entry in "
            "c/test-map.toml.\n\n"
        )
    crate, stem = port.crate, port.module
    if port_units is PortUnits.TESTS:
        tests = "\n".join(f"- {t}" for t in port.tests)
        return (
            f"Translate these Rust tests to cases of c/tests/{stem}.c, each a `static void` "
            "function named as the Rust test and run with CCM_RUN, so its CTest test has the "
            f"Rust test's name:\n{tests}\n\nThen delete the Rust tests, and map each in "
            "c/test-map.toml.\n\n"
        )
    if port_units is PortUnits.EXECUTABLE:
        return (
            f"Port the program {port.bin or stem} to `int {crate}_{stem}_main(int argc, char "
            f"**argv)` in c/src/{stem}.c (declared in c/include/{crate}/{stem}.h), with the "
            "same output and exit status, and make the Rust program a façade that calls it "
            "with its arguments and exits with its status.\n\n"
        )
    callers = ", ".join(port.callers) or "none"
    exports = "\n".join(f"- {e}" for e in port.exports) or "- (none)"
    return (
        f"Port the module to C in c/src/{stem}.c, with its interface in "
        f"c/include/{crate}/{stem}.h, and make the module a Rust façade over it: each public "
        "item keeps its signature and calls the C through `sys` (bindgen writes "
        "src/.../sys.rs from the header before each build; don't edit it), the one place "
        f"unsafe code may go. These files keep using the module through its Rust API and "
        f"don't change: {callers}. Map each public item in c/symbol-map.toml (read it first: "
        f"it also says where earlier modules went):\n{exports}\n\n"
    )


_TARGET_NAMES = {"c": "C", "cpp": "C++", "rust": "Rust"}


def csharp_port_note(unit: WorkUnit, port_units: PortUnits | None, target: str) -> str:
    """A port from C#'s unit (D-78, D-79): which C# becomes a façade, goes or stays, which C#
    keeps using it, and what to map, by ID. The unit's own note says what it ports and the
    rule's guidance how; this is the same for every target but for its name and the
    directory of its maps (``cpp/``, ``rust/``)."""
    name = _TARGET_NAMES.get(target, target)
    symbols, tests = f"{target}/symbol-map.toml", f"{target}/test-map.toml"
    port = unit.port
    if port_units is PortUnits.RETIREMENT or port is None:
        baseline = (
            f"Give every baseline test that no longer runs an entry in {tests}: the earlier "
            "units mapped theirs, so add the ones still missing (a data-driven C# test's cases "
            "by the IDs its test runs reported), each mapped test passing."
        )
        if unit.kept_api:
            one = len(unit.kept_api) == 1
            return (
                f"The codemod deleted the C# programs, which are {name} programs now, and the "
                f"test projects that ran them ({len(unit.files)} files). "
                f"{', '.join(unit.kept_api)} {'stays' if one else 'stay'} as the .NET library "
                f"over the {name} (keep_source_api), with the façades, the bridge, the façades' "
                "support and the C# tests of the kept API: leave them as they are, since both "
                f"builds run in the verification. {baseline}\n\n"
            )
        return (
            f"The codemod retired the C#: it deleted {len(unit.files)} C# and MSBuild files, the "
            f"façades' support and the bridge, so the {name} builds and tests alone. Make it "
            "build, pass and lint clean on its own (the checks now cover all of it, not only "
            f"what this unit changes), and fix what still refers to the C# or the bridge. "
            f"{baseline}\n\n"
        )
    sources = [f for f in unit.files if f.lower().endswith(".cs")]
    members = "\n".join(f"- {e}" for e in port.exports) or "- (none)"
    if port_units is PortUnits.TESTS:
        cases = "\n".join(f"- {t}" for t in port.tests) or "- (none but the note's)"
        return (
            "The C# test files are read-only and keep running until the retirement: "
            f"{', '.join(unit.reference_files or sources)}. Map each of these cases in {tests} "
            f'(`csharp = "dotnet:..."`, `{target} = ...`) to the {name} tests that translate '
            f"it, each passing:\n{cases}\n\n"
        )
    if port_units is PortUnits.EXECUTABLE:
        return (
            "The C# file with `Main` becomes the program's façade: `Main` keeps its signature, "
            f"calls `{port.crate}_{port.module}_main` with its arguments and exits with its "
            f"code. Delete the program's other C# files once the {name} does their job (of "
            f"{', '.join(sources)}). Map each member of its types in {symbols} (read it first: "
            f"it also says where earlier units went):\n{members}\n\n"
        )
    callers = ", ".join(port.callers) or "none"
    return (
        f"The façades: {', '.join(sources)}. Each C# type keeps every public and protected "
        "member with its signature, modifiers, XML docs and attributes, its body calling the "
        "bridge through `CcmNative` (CodeCoreMorph.Native.g.cs, generated from the bridge's "
        "headers at each configure: never edit it). These files keep using the types through "
        f"the façades and don't change: {callers}. Map each of these members in {symbols} by "
        f"its ID (read it first: it also says where earlier units went):\n{members}\n\n"
    )


def _rust_port(rules: Sequence[LoadedRule]) -> bool:
    """Whether the unit ports Rust (to C, D-75)."""
    return any(r.rule.kind is RuleKind.PORT and r.rule.language == "rust" for r in rules)


def _c_port(rules: Sequence[LoadedRule]) -> bool:
    """Whether the unit ports C (D-68), whose programs and tests build with Make."""
    return any(r.rule.kind is RuleKind.PORT and r.rule.language == "c" for r in rules)


def _csharp_target(rules: Sequence[LoadedRule]) -> str | None:
    """The target language when the unit ports C# (D-78)."""
    return next(
        (
            r.rule.target_language
            for r in rules
            if r.rule.kind is RuleKind.PORT and r.rule.language == "csharp"
        ),
        None,
    )


def _port_units(rules: Sequence[LoadedRule]) -> PortUnits | None:
    return next(
        (r.rule.port_units for r in rules if r.rule.kind is RuleKind.PORT and r.rule.port_units),
        None,
    )


def build_context(
    unit: WorkUnit,
    rules: Sequence[LoadedRule],
    *,
    worktree: Path,
    index_path: Path | None,
    language: str,
    source: str,
    target: str,
    codemods: CodemodResult | None,
    remaining: Mapping[str, Sequence[RuleSite]],
    build: str | None,
    tools: Sequence[ToolSpec],
    token_budget: int,
) -> UnitContext:
    system = system_prompt(rules, language=language, source=source, target=target)
    message = unit_message(
        unit,
        worktree=worktree,
        index_path=index_path,
        codemods=codemods,
        remaining=remaining,
        build=build,
        port=any(r.rule.kind is RuleKind.PORT for r in rules),
        port_units=_port_units(rules),
        c_port=_c_port(rules),
        rust_port=_rust_port(rules),
        csharp_target=_csharp_target(rules),
    )
    tool_chars = sum(len(t.model_dump_json()) for t in tools)
    tokens = estimate_tokens(sum(len(b.text) for b in system) + len(message) + tool_chars)
    if tokens > token_budget:
        raise ContextOverflowError(
            f"work unit {unit.id} needs about {tokens:,} tokens of context",
            reason=f"the editor model's budget is {token_budget:,} tokens; files are never "
            "truncated",
            hint="Split the unit (fewer files), or use a model with a larger context window.",
        )
    return UnitContext(
        system=system, first_message=Message.user(message, cache_breakpoint=True), tokens=tokens
    )


def host_document(
    unit: WorkUnit,
    rules: Sequence[LoadedRule],
    *,
    worktree: Path,
    index_path: Path | None,
    language: str,
    source: str,
    target: str,
    codemods: CodemodResult | None,
    remaining: Mapping[str, Sequence[RuleSite]],
    build: str | None,
    attempt: int,
    attempts: int,
    status: str,
    edits_file: str,
    uncompiled: Sequence[str] = (),
) -> str:
    """The context document of a host agent's attempt (D-41): instructions, rules, unit.

    A project rule's unit (the standard bump) also lists ``uncompiled``: code the build never
    compiles, which the bump may break unnoticed (D-42)."""
    instructions = render(
        "host_system", language=language, source=source, target=target, edits_file=edits_file
    )
    message = unit_message(
        unit,
        worktree=worktree,
        index_path=index_path,
        codemods=codemods,
        remaining=remaining,
        build=build,
        find_symbol="`codecoremorph unit symbol`",
        port=any(r.rule.kind is RuleKind.PORT for r in rules),
        port_units=_port_units(rules),
        c_port=_c_port(rules),
        rust_port=_rust_port(rules),
        csharp_target=_csharp_target(rules),
    )
    if uncompiled and any(r.rule.kind is RuleKind.PROJECT for r in rules):
        shown = [f"- {f}" for f in uncompiled[:MAX_UNCOMPILED]]
        if len(uncompiled) > MAX_UNCOMPILED:
            shown.append(f"- … and {len(uncompiled) - MAX_UNCOMPILED} more")
        message = "\n\n".join(
            [
                message.strip(),
                "## Files this build doesn't compile",
                "This build configuration never compiles these files (another platform's "
                "sources, disabled targets), so neither the build nor the tests check them. "
                "The unit may edit them. When the build reports an error, look for the same "
                "code in them and fix it the same way, and name in review_notes the ones you "
                "changed: nothing verifies those changes.",
                "\n".join(shown),
            ]
        )
    return render(
        "host_unit",
        unit_id=unit.id,
        attempt=str(attempt),
        attempts=str(attempts),
        status=status.strip(),
        instructions=instructions.strip(),
        rules=_demote(rules_section(rules, nest_guidance=2)),
        unit=_demote(message.strip()),
    )


def _demote(markdown: str, levels: int = 1) -> str:
    """Nest a message's headings ``levels`` deeper, outside code fences."""
    if levels <= 0:
        return markdown
    lines: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        match = re.match(r"^(`{3,})", line)
        if match:
            if fence is None:
                fence = match.group(1)
            elif line.strip() == fence:
                fence = None
        elif fence is None and line.startswith("#"):
            line = "#" * levels + line
        lines.append(line)
    return "\n".join(lines)
