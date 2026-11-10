"""The C++ side of a C# → C++ port (SPEC §11.9, D-78): names, the CMake files, the runtime and
the scaffold of a type unit."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext, AdapterNotSupportedError
from codecoremorph.adapters.c import CAdapter
from codecoremorph.adapters.cpp import CppAdapter, csharpports
from codecoremorph.adapters.cpp.pinvoke import GENERATED_FILE
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LanguageConfig

from .test_make import ScriptedExecutor

OPTIONS = {
    "crate": "shop",
    "build_target": "src/Shop/Shop.csproj",
    "module": "money",
    "types": "csharp:Shop.Money",
}


@pytest.mark.parametrize(
    ("project", "name"),
    [
        ("src/Shop/Shop.csproj", "shop"),
        ("OrderBook", "order_book"),
        ("Order.Book.csproj", "order_book"),
        ("HTTPClient.csproj", "http_client"),
        ("src\\My-Lib\\My-Lib.CSPROJ", "my_lib"),
        ("2Fast.csproj", "cs_2_fast"),
    ],
)
def test_library_names(project: str, name: str) -> None:
    assert csharpports.library_name(project) == name


def test_unit_names() -> None:
    assert csharpports.unit_name("Zoo.OrderBook", ()) == "order_book"
    assert csharpports.unit_name("Zoo.Pen`1", ()) == "pen"
    assert csharpports.unit_name("IFeeder", ()) == "i_feeder"
    assert csharpports.unit_name("Zoo.Item", ("item",)) == "item_2"
    assert csharpports.unit_name("Zoo.Item", ("item", "item_2")) == "item_3"
    # The runtime's files keep their names.
    assert csharpports.unit_name("Zoo.CodecoremorphBridge", ()) == "codecoremorph_bridge_type"


def test_type_names_for_the_stubs() -> None:
    assert csharpports.type_names("csharp:Zoo.Pen`1,csharp:Zoo.Pens") == "Zoo.Pen, Zoo.Pens"
    assert csharpports.type_names("") == ""


def test_the_cpp_adapter_names_a_csharp_ports_units() -> None:
    """The planner asks the target adapter (D-58): the C++ libraries from the C# project, the
    unit from its first type. The C adapter, the target of a port from Rust, names none."""
    assert CppAdapter().port_names("src/Zoo/Zoo.csproj", "Zoo.Animal", ()) == ("zoo", "animal")
    with pytest.raises(AdapterNotSupportedError):
        CAdapter().port_names("src/Zoo/Zoo.csproj", "Zoo.Animal", ())


def test_the_standard_is_cpp17_or_newer() -> None:
    assert csharpports.cxx_standard("cpp17") == "17"
    assert csharpports.cxx_standard("cpp23") == "23"
    with pytest.raises(ConfigError):
        csharpports.cxx_standard("cpp11")


def test_the_cmake_files() -> None:
    top = csharpports.top_cmake("shop", "src/Shop/Shop.csproj", "cpp20")
    assert top.startswith(csharpports.TOP_MARK)
    assert "set(CMAKE_CXX_STANDARD 20)" in top
    assert "option(CODECOREMORPH_SANITIZE" in top
    assert "set(CMAKE_CXX_VISIBILITY_PRESET hidden)" in top
    cpp = csharpports.cpp_cmake("shop", "Shop", ["src/b.cpp", "src/a.cpp"], ["bridge/x.cpp"])
    assert csharpports.listed(cpp) == ["src/a.cpp", "src/b.cpp"]
    assert csharpports.listed(cpp, bridge=True) == ["bridge/x.cpp"]
    assert csharpports.native_name(cpp) == "shop_native"
    assert "target_link_options(shop_native PRIVATE -shared-libasan)" in cpp
    added = csharpports.with_listed(cpp, "src/c.cpp")
    assert added is not None
    assert csharpports.listed(added) == [
        "src/a.cpp",
        "src/b.cpp",
        "src/c.cpp",
    ]
    assert csharpports.with_listed(added, "src/c.cpp") == added
    bridged = csharpports.with_listed(cpp, "bridge/y.cpp", bridge=True)
    assert bridged is not None
    assert csharpports.listed(bridged, bridge=True) == ["bridge/x.cpp", "bridge/y.cpp"]
    assert csharpports.with_listed("add_library(x STATIC a.cpp)", "b.cpp") is None


def _shop(tmp_path: Path) -> Path:
    project = tmp_path / "src" / "Shop"
    project.mkdir(parents=True)
    (project / "Shop.csproj").write_text("<Project />\n", encoding="utf-8")
    return tmp_path


def test_the_first_unit_writes_the_build_the_runtime_and_the_support(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    changed = csharpports.scaffold(root, "cpp-csharp-type", OPTIONS, [], "cpp17")
    assert set(changed) == {
        "CMakeLists.txt",
        "cpp/CMakeLists.txt",
        "cpp/include/codecoremorph_decimal.hpp",
        "cpp/include/codecoremorph_exceptions.hpp",
        "cpp/bridge/codecoremorph_bridge.h",
        "cpp/bridge/codecoremorph_bridge.hpp",
        "cpp/bridge/codecoremorph_bridge.cpp",
        "cpp/bridge/money.h",
        "cpp/bridge/money.cpp",
        "cpp/src/money.hpp",
        "cpp/src/money.cpp",
        "cpp/symbol-map.toml",
        "src/Shop/CodeCoreMorph.Interop.cs",
        f"src/Shop/{GENERATED_FILE}",
    }
    assert csharpports.is_port(root)
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.listed(cmake) == ["src/money.cpp"]
    assert csharpports.listed(cmake, bridge=True) == [
        "bridge/codecoremorph_bridge.cpp",
        "bridge/money.cpp",
    ]
    assert csharpports.bridge_headers(root) == ["cpp/bridge/money.h"]
    assert csharpports.facade_projects(root) == ["src/Shop"]
    generated = (root / "src/Shop" / GENERATED_FILE).read_text(encoding="utf-8")
    assert 'internal const string Library = "shop_native";' in generated
    support = (root / "src/Shop/CodeCoreMorph.Interop.cs").read_text(encoding="utf-8")
    assert support == csharpports.runtime_text("CodeCoreMorph.Interop.cs")
    assert "namespace CodeCoreMorph.Interop" in support
    header = (root / "cpp/bridge/money.h").read_text(encoding="utf-8")
    assert '#include "codecoremorph_bridge.h"' in header
    assert 'extern "C"' in header
    # Again: nothing to do.
    assert csharpports.scaffold(root, "cpp-csharp-type", OPTIONS, [], "cpp17") == []


def test_a_later_unit_adds_its_files_and_keeps_the_rest(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    csharpports.scaffold(root, "cpp-csharp-type", OPTIONS, [], "cpp17")
    (root / "cpp/src/money.cpp").write_text("// ported\n", encoding="utf-8")
    changed = csharpports.scaffold(
        root,
        "cpp-csharp-type",
        {**OPTIONS, "module": "item", "types": "csharp:Shop.Item"},
        [],
        "cpp17",
    )
    assert set(changed) == {
        "cpp/CMakeLists.txt",
        "cpp/bridge/item.h",
        "cpp/bridge/item.cpp",
        "cpp/src/item.hpp",
        "cpp/src/item.cpp",
    }
    assert (root / "cpp/src/money.cpp").read_text(encoding="utf-8") == "// ported\n"
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.listed(cmake) == ["src/item.cpp", "src/money.cpp"]
    assert csharpports.bridge_headers(root) == ["cpp/bridge/item.h", "cpp/bridge/money.h"]


@pytest.mark.parametrize(
    "options",
    [
        {**OPTIONS, "crate": "Shop"},
        {**OPTIONS, "module": "../x"},
        {**OPTIONS, "build_target": "src/Shop/Missing.csproj"},
        {**OPTIONS, "build_target": "src/Shop/Money.cs"},
    ],
)
def test_bad_options_are_refused(tmp_path: Path, options: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        csharpports.scaffold(_shop(tmp_path), "cpp-csharp-type", options, [], "cpp17")


def test_facade_projects_skip_build_output(tmp_path: Path) -> None:
    root = _shop(tmp_path)
    (root / "src/Shop" / csharpports.SUPPORT_FILE).write_text("", encoding="utf-8")
    stale = root / "src/Shop/obj/Debug"
    stale.mkdir(parents=True)
    (stale / csharpports.SUPPORT_FILE).write_text("", encoding="utf-8")
    assert csharpports.facade_projects(root) == ["src/Shop"]


def test_the_test_host_environment() -> None:
    env = csharpports.test_environment("/b/lib", "/llvm/libclang_rt.asan.so")
    assert env["LD_LIBRARY_PATH"] == "/b/lib"
    assert env["LD_PRELOAD"] == "/llvm/libclang_rt.asan.so"
    assert "handle_segv=0" in env["ASAN_OPTIONS"]  # CoreCLR keeps its own SIGSEGV handling
    assert "detect_leaks=0" in env["ASAN_OPTIONS"]
    assert csharpports.test_environment("/b/lib", None) == {"LD_LIBRARY_PATH": "/b/lib"}


def test_the_target_of_a_csharp_port_names_the_image_with_both_toolchains() -> None:
    lang = LanguageConfig(language="cpp", standard="cpp17", port_source="csharp")
    spec = CppAdapter().toolchain_requirements(lang)
    assert spec.image == "codecoremorph-csharp-port"
    assert spec.language == "csharp-port"
    assert "dotnet" in {t.name for t in spec.tools}
    # C++ alone, and C, aren't affected.
    assert (
        CppAdapter().toolchain_requirements(lang.model_copy(update={"port_source": None})).image
        == "codecoremorph-cpp"
    )
    c = LanguageConfig(language="c", standard="c99", port_source="csharp", build_system="cmake")
    assert CAdapter().toolchain_requirements(c).image != "codecoremorph-csharp-port"
    assert "cpp-csharp-type" in CppAdapter().capabilities().codemod_tools


PROGRAM = {"crate": "shop", "module": "shop_cli", "build_target": "src/Cli/Shop.Cli.csproj",
           "bin": "Shop.Cli"}  # fmt: skip


def _solution(tmp_path: Path) -> Path:
    """The shop with a program and a test project, one type ported."""
    root = _shop(tmp_path)
    for project in ("src/Cli/Shop.Cli.csproj", "tests/Shop.Tests/Shop.Tests.csproj"):
        (root / project).parent.mkdir(parents=True)
        (root / project).write_text("<Project />\n", encoding="utf-8")
    csharpports.scaffold(root, "cpp-csharp-type", OPTIONS, [], "cpp17")
    return root


def test_a_program_becomes_a_function_of_the_library_and_a_cmake_program(
    tmp_path: Path,
) -> None:
    root = _solution(tmp_path)
    changed = csharpports.scaffold(root, "cpp-csharp-program", PROGRAM, [], "cpp17")
    assert set(changed) == {
        "cpp/CMakeLists.txt",
        "cpp/src/shop_cli_main.hpp",
        "cpp/src/shop_cli_main.cpp",
        "cpp/bridge/shop_cli.h",
        "cpp/bridge/shop_cli.cpp",
        "cpp/programs/shop_cli.cpp",
        # The program's façade calls the export: it gets the support too.
        "src/Cli/CodeCoreMorph.Interop.cs",
        f"src/Cli/{GENERATED_FILE}",
    }
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.listed(cmake) == ["src/money.cpp", "src/shop_cli_main.cpp"]
    assert "bridge/shop_cli.cpp" in csharpports.listed(cmake, bridge=True)
    assert csharpports.programs(cmake) == {"Shop.Cli": "shop_cli"}
    assert "target_link_libraries(Shop.Cli PRIVATE shop_cpp)" in cmake
    header = (root / "cpp/src/shop_cli_main.hpp").read_text(encoding="utf-8")
    assert "int shop_cli_main(const std::vector<std::string>& args);" in header
    entry = (root / "cpp/programs/shop_cli.cpp").read_text(encoding="utf-8")
    assert "return shop_cli_main(std::vector<std::string>(argv + 1, argv + argc));" in entry
    bridge = (root / "cpp/bridge/shop_cli.h").read_text(encoding="utf-8")
    assert "CCM_EXPORT ccm_status shop_shop_cli_main(const char **args_data," in bridge
    body = (root / "cpp/bridge/shop_cli.cpp").read_text(encoding="utf-8")
    assert "*result = shop_cli_main(args);" in body
    assert "std::cout.flush();" in body
    assert csharpports.facade_projects(root) == ["src/Cli", "src/Shop"]
    # Again: nothing to do; a second program joins the list.
    assert csharpports.scaffold(root, "cpp-csharp-program", PROGRAM, [], "cpp17") == []
    other = {**PROGRAM, "module": "admin", "bin": "Admin"}
    csharpports.scaffold(root, "cpp-csharp-program", other, [], "cpp17")
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.programs(cmake) == {"Admin": "admin", "Shop.Cli": "shop_cli"}
    assert cmake.count(csharpports.PROGRAMS_BEGIN) == 1


def test_a_program_needs_a_cmake_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        csharpports.scaffold(
            _solution(tmp_path), "cpp-csharp-program", {**PROGRAM, "bin": "Shop Cli"}, [], "cpp17"
        )


def test_a_test_file_becomes_a_googletest_program(tmp_path: Path) -> None:
    root = _solution(tmp_path)
    options = {"crate": "shop", "module": "cart_tests", "build_target": "tests/Shop.Tests"}
    files = ["tests/Shop.Tests/CartTests.cs", "tests/Shop.Tests/FakeStock.cs"]
    changed = csharpports.scaffold(root, "cpp-csharp-tests", options, files, "cpp17")
    assert set(changed) == {
        "cpp/test-map.toml",
        "cpp/tests/CMakeLists.txt",
        "cpp/tests/cart_tests.cpp",
    }
    stub = (root / "cpp/tests/cart_tests.cpp").read_text(encoding="utf-8")
    assert stub.startswith("// The tests of tests/Shop.Tests/CartTests.cs, translated from C#")
    assert "#include <gtest/gtest.h>" in stub
    tests = (root / "cpp/tests/CMakeLists.txt").read_text(encoding="utf-8")
    assert "find_package(GTest REQUIRED)" in tests
    assert "target_link_libraries(${program} PRIVATE shop_cpp ${_ccm_gtest})" in tests
    assert "add_test(NAME ${program} COMMAND ${program})" in tests
    assert csharpports.test_programs(tests) == ["cart_tests"]
    # The library's CMake file takes the tests' directory once it exists.
    cpp = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_subdirectory(tests)" in cpp
    # A run passes no files: a test unit's C# files are all read-only references.
    csharpports.scaffold(root, "cpp-csharp-tests", {**options, "module": "money_tests"}, [],
                         "cpp17")  # fmt: skip
    tests = (root / "cpp/tests/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.test_programs(tests) == ["cart_tests", "money_tests"]
    stub = (root / "cpp/tests/money_tests.cpp").read_text(encoding="utf-8")
    assert stub.startswith("// The tests of a C# test file (the unit's note names it), translated")
    map_text = (root / "cpp/test-map.toml").read_text(encoding="utf-8")
    assert map_text == csharpports.TEST_MAP_TEXT


def test_tests_come_after_the_types(tmp_path: Path) -> None:
    options = {"crate": "shop", "module": "cart_tests"}
    with pytest.raises(ConfigError):
        csharpports.scaffold(_shop(tmp_path), "cpp-csharp-tests", options, [], "cpp17")


SLN = (
    "﻿\r\n"
    "Microsoft Visual Studio Solution File, Format Version 12.00\r\n"
    'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Shop", "src\\Shop\\Shop.csproj", '
    '"{11111111-1111-1111-1111-111111111111}"\r\n'
    "EndProject\r\n"
    'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Shop.Cli", '
    '"src\\Cli\\Shop.Cli.csproj", "{22222222-2222-2222-2222-222222222222}"\r\n'
    "\tProjectSection(ProjectDependencies) = postProject\r\n"
    "\tEndProjectSection\r\n"
    "EndProject\r\n"
    "Global\r\n"
    "\tGlobalSection(ProjectConfigurationPlatforms) = postSolution\r\n"
    "\t\t{11111111-1111-1111-1111-111111111111}.Debug|Any CPU.ActiveCfg = Debug|Any CPU\r\n"
    "\t\t{22222222-2222-2222-2222-222222222222}.Debug|Any CPU.ActiveCfg = Debug|Any CPU\r\n"
    "\tEndGlobalSection\r\n"
    "EndGlobal\r\n"
)


def test_a_solution_drops_the_deleted_projects() -> None:
    kept = csharpports.without_projects("Shop.sln", SLN, ["src/Cli/Shop.Cli.csproj"])
    assert "Shop.Cli" not in kept
    assert "22222222" not in kept
    assert '"src\\Shop\\Shop.csproj"' in kept
    assert "{11111111-1111-1111-1111-111111111111}.Debug" in kept
    assert kept.count("\r\n") == SLN.count("\r\n") - 5  # its line endings kept
    assert "\n" not in kept.replace("\r\n", "")
    assert csharpports.without_projects("Shop.sln", SLN, []) == SLN
    slnx = (
        "<Solution>\n"
        '  <Project Path="src/Shop/Shop.csproj" />\n'
        '  <Project Path="src/Cli/Shop.Cli.csproj">\n'
        '    <Build Project="false" />\n'
        "  </Project>\n"
        "</Solution>\n"
    )
    assert csharpports.without_projects("Shop.slnx", slnx, ["src/Cli/Shop.Cli.csproj"]) == (
        '<Solution>\n  <Project Path="src/Shop/Shop.csproj" />\n</Solution>\n'
    )


def _ported(tmp_path: Path) -> tuple[Path, list[str]]:
    """The solution with its program and tests ported; the unit's files are every C# file and
    the build."""
    root = _solution(tmp_path)
    csharpports.scaffold(root, "cpp-csharp-program", PROGRAM, [], "cpp17")
    csharpports.scaffold(root, "cpp-csharp-tests", {"crate": "shop", "module": "cart_tests"},
                         ["tests/Shop.Tests/CartTests.cs"], "cpp17")  # fmt: skip
    files = {
        "src/Shop/Money.cs": "namespace Shop { }\n",
        "src/Shop/CcmExceptions.cs": "namespace CodeCoreMorph.Interop { }\n",
        "src/Cli/Program.cs": "class Program { }\n",
        "tests/Shop.Tests/CartTests.cs": "class CartTests { }\n",
        "Shop.sln": SLN,
        "Directory.Build.props": "<Project />\n",
        "global.json": "{}\n",
        "README.md": "# Shop\n",
    }
    for rel, text in files.items():
        (root / rel).write_bytes(text.encode("utf-8"))
    unit = [
        "Directory.Build.props",
        "Shop.sln",
        "global.json",
        "src/Cli/Program.cs",
        "src/Cli/Shop.Cli.csproj",
        "src/Shop/Money.cs",
        "src/Shop/Shop.csproj",
        "tests/Shop.Tests/CartTests.cs",
        "tests/Shop.Tests/Shop.Tests.csproj",
    ]
    return root, unit


def test_the_retirement_leaves_a_cmake_project(tmp_path: Path) -> None:
    root, files = _ported(tmp_path)
    changed = csharpports.scaffold(root, "cpp-csharp-retire", {"keep": "false"}, files, "cpp17")
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert left == [
        "CMakeLists.txt",
        "README.md",
        "cpp/CMakeLists.txt",
        "cpp/include/codecoremorph_decimal.hpp",
        "cpp/include/codecoremorph_exceptions.hpp",
        "cpp/programs/shop_cli.cpp",
        "cpp/src/money.cpp",
        "cpp/src/money.hpp",
        "cpp/src/shop_cli_main.cpp",
        "cpp/src/shop_cli_main.hpp",
        "cpp/symbol-map.toml",
        "cpp/test-map.toml",
        "cpp/tests/CMakeLists.txt",
        "cpp/tests/cart_tests.cpp",
    ]
    assert not (root / "src").exists()  # emptied, so removed
    assert not (root / "tests").exists()
    assert "src/Shop/CcmExceptions.cs" in changed  # the support goes with the façades
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.native_name(cmake) is None
    assert csharpports.BRIDGE_BEGIN not in cmake
    assert csharpports.listed(cmake) == ["src/money.cpp", "src/shop_cli_main.cpp"]
    assert csharpports.programs(cmake) == {"Shop.Cli": "shop_cli"}
    assert "add_subdirectory(tests)" in cmake


def test_keep_source_api_retires_the_programs_only(tmp_path: Path) -> None:
    root, _ = _ported(tmp_path)
    files = ["Shop.sln", "src/Cli/Program.cs", "src/Cli/Shop.Cli.csproj"]
    csharpports.scaffold(root, "cpp-csharp-retire", {"keep": "true"}, files, "cpp17")
    assert not (root / "src/Cli").exists()
    kept = [
        "src/Shop/Money.cs",
        "src/Shop/Shop.csproj",
        "src/Shop/CcmExceptions.cs",
        "src/Shop/CodeCoreMorph.Interop.cs",
        f"src/Shop/{GENERATED_FILE}",
        "tests/Shop.Tests/CartTests.cs",
        "Directory.Build.props",
        "global.json",
        "cpp/bridge/money.h",
        "cpp/bridge/codecoremorph_bridge.cpp",
    ]
    assert [k for k in kept if not (root / k).is_file()] == []
    solution = (root / "Shop.sln").read_bytes().decode("utf-8")
    assert "Shop.Cli" not in solution
    assert '"src\\Shop\\Shop.csproj"' in solution
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert csharpports.native_name(cmake) == "shop_native"


def test_every_scaffold_is_the_cpp_adapters() -> None:
    tools = CppAdapter().capabilities().codemod_tools
    for tool in ("cpp-csharp-type", "cpp-csharp-program", "cpp-csharp-tests", "cpp-csharp-retire"):
        assert tool in tools


@pytest.mark.parametrize(
    ("code", "lines"),
    [
        ("auto p = std::make_shared<Item>(1);\n", []),
        ("Item* p = new Item(1);\n", [1]),
        ("int* xs = new int[3];\ndelete[] xs;\n", [1, 2]),
        ("std::unique_ptr<Item> p(new Item(1));\n", []),
        ("auto p = std::shared_ptr<Item>(new Item(1));\n", []),  # a private constructor's way
        ("item_.reset(new Item(1));\n", []),
        ("Item(const Item&) = delete;\nvoid operator delete(void* p);\n", []),
        ("void* operator new(std::size_t n);\n", []),
        ('// new Item(1)\nconst char* s = "delete x";\n', []),  # comments and strings
        ("delete item;\n", [1]),
    ],
)
def test_owning_raw_pointers(code: str, lines: list[int]) -> None:
    assert [line for line, _ in csharpports.raw_ownership(code)] == lines


LINT_LANG = LanguageConfig(
    language="cpp", standard="cpp17", port_source="csharp", build_system="cmake"
)


def _lint_context(tmp_path: Path, tidy: str) -> tuple[AdapterContext, ScriptedExecutor]:
    work = tmp_path / "work"
    _solution(work)
    csharpports.scaffold(work, "cpp-csharp-program", PROGRAM, [], "cpp17")
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    # Configure's copy: in Docker the build directory is a volume the host doesn't see.
    (tmp_path / "cache" / "compile_commands.json").write_text("[]\n", encoding="utf-8")
    executor = ScriptedExecutor(
        lambda argv, env, cwd: (1, tidy) if argv[0] == "clang-tidy" else (0, "")
    )
    ctx = AdapterContext(
        worktree=work,
        build_dir=tmp_path / "build",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        executor=executor,  # type: ignore[arg-type]
        lang=LINT_LANG,
    )
    return ctx, executor


def test_the_lint_of_a_csharp_ports_cpp(tmp_path: Path) -> None:
    """``lint_clean`` on the C++ of a C# port (D-78): clang-tidy's findings and -Wall -Wextra's
    warnings, owning raw pointers and pointer casts outside the bridge; the bridge's own casts
    and the runtime's code don't count."""
    work = (tmp_path / "work").as_posix()
    tidy = "\n".join(
        [
            f"{work}/cpp/src/money.cpp:3:12: warning: do not use reinterpret_cast "
            "[cppcoreguidelines-pro-type-reinterpret-cast]",
            f"{work}/cpp/bridge/money.cpp:9:12: warning: do not use reinterpret_cast "
            "[cppcoreguidelines-pro-type-reinterpret-cast]",
            f"{work}/cpp/src/money.cpp:4:9: warning: unused variable 'x' "
            "[clang-diagnostic-unused-variable]",
            f"{work}/cpp/bridge/codecoremorph_bridge.hpp:40:5: warning: a finding "
            "[bugprone-macro-parentheses]",
            "/usr/include/c++/14/vector:12:1: warning: not ours [bugprone-sizeof-expression]",
        ]
    )
    ctx, executor = _lint_context(tmp_path, tidy)
    (ctx.worktree / "cpp/src/money.cpp").write_text(
        '#include "money.hpp"\n\nint* leak() { return new int(1); }\n', encoding="utf-8"
    )
    (ctx.worktree / "cpp/bridge/money.cpp").write_text(
        "void* handle() { return new int(3); }\n", encoding="utf-8"
    )  # the bridge makes its handles
    found = CppAdapter().lint(ctx)
    assert sorted((d.file_path, d.line, d.code) for d in found) == [
        ("cpp/src/money.cpp", 3, "codecoremorph-owning-memory"),
        ("cpp/src/money.cpp", 3, "cppcoreguidelines-pro-type-reinterpret-cast"),
        ("cpp/src/money.cpp", 4, "clang-diagnostic-unused-variable"),
    ]
    cast = next(d for d in found if d.code == "cppcoreguidelines-pro-type-reinterpret-cast")
    assert "outside the bridge, the C++ has no casts between pointer types" in cast.message
    [(argv, _)] = executor.commands
    assert argv[0] == "clang-tidy"
    assert any(a.startswith("--checks=-*,clang-diagnostic-*,bugprone-*") for a in argv)
    checked = [a.removeprefix(f"{work}/") for a in argv if a.startswith(f"{work}/cpp/")]
    assert checked == [
        "cpp/bridge/money.cpp",
        "cpp/bridge/shop_cli.cpp",
        "cpp/programs/shop_cli.cpp",
        "cpp/src/money.cpp",
        "cpp/src/shop_cli_main.cpp",
    ]  # the runtime's codecoremorph_bridge.cpp isn't the port's


def test_a_units_lint_runs_clang_tidy_over_its_own_files(tmp_path: Path) -> None:
    """A unit's ``lint_clean`` lints what covers its new code, not every file earlier units
    wrote (D-83): at libsodium-core's last unit that was 165 s of 98 files against 2.5 s."""
    ctx, executor = _lint_context(tmp_path, "")
    (ctx.worktree / "cpp/src/money.cpp").write_text('#include "money.hpp"\n', encoding="utf-8")
    CppAdapter().lint(ctx, {"cpp/src/money.hpp", "cpp/src/money.cpp", "cpp/symbol-map.toml"})
    [(argv, _)] = executor.commands
    work = (tmp_path / "work").as_posix()
    assert [a.removeprefix(f"{work}/") for a in argv if a.startswith(f"{work}/cpp/")] == [
        "cpp/src/money.cpp"
    ]


def test_the_lint_keeps_the_sanitizers(tmp_path: Path) -> None:
    ctx, _ = _lint_context(tmp_path, "")
    top = ctx.worktree / "CMakeLists.txt"
    text = top.read_text(encoding="utf-8")
    assert CppAdapter().lint(ctx) == []
    top.write_text(
        "\n".join(line for line in text.splitlines() if csharpports.SANITIZE not in line) + "\n",
        encoding="utf-8",
    )
    [problem] = CppAdapter().lint(ctx)
    assert problem.file_path == "CMakeLists.txt"
    assert "the CODECOREMORPH_SANITIZE option is gone" in problem.message


def test_cpp_alone_has_no_linter(tmp_path: Path) -> None:
    ctx, _ = _lint_context(tmp_path, "")
    with pytest.raises(AdapterNotSupportedError):
        CppAdapter().lint(replace(ctx, lang=LINT_LANG.model_copy(update={"port_source": None})))


def test_the_programs_and_tests_build_with_warnings_on(tmp_path: Path) -> None:
    root = _solution(tmp_path)
    csharpports.scaffold(root, "cpp-csharp-program", PROGRAM, [], "cpp17")
    cmake = (root / "cpp/CMakeLists.txt").read_text(encoding="utf-8")
    assert "target_compile_options(Shop.Cli PRIVATE -Wall -Wextra)" in cmake
    assert "target_compile_options(${program} PRIVATE -Wall -Wextra)" in csharpports.tests_cmake(
        "shop", ["cart_tests"]
    )
