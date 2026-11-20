"""The C++ side of a Java → C++ port (SPEC §11.12, D-92): names, the CMake files with the JNI
glue, the runtime, the Java support in the façade module, and the type, tests and retirement
scaffolds."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.cpp import CppAdapter, javaports
from codecoremorph.adapters.java import facades
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LanguageConfig
from codecoremorph.invariants.checks import check_build_commands, patch_lines

OPTIONS = {
    "crate": "shop",
    "build_target": "pom.xml",
    "module": "money",
    "types": "java:shop.Money",
}
POM = """<project>
  <modelVersion>4.0.0</modelVersion>
  <parent><groupId>org.example</groupId><artifactId>parent</artifactId><version>1</version></parent>
  <artifactId>shop</artifactId>
  <dependencies>
    <dependency><groupId>junit</groupId><artifactId>junit</artifactId></dependency>
  </dependencies>
</project>
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "src/main/java/shop").mkdir(parents=True)
    (root / "pom.xml").write_text(POM, encoding="utf-8")
    (root / "src/main/java/shop/Money.java").write_text("package shop;\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("naming", "name"),
    [("shop.jar", "shop"), ("order-book.jar", "order_book"), ("core/pom.xml", "core")],
)
def test_library_names(naming: str, name: str) -> None:
    assert facades.library_name(naming) == name


def test_the_cpp_adapter_names_a_java_ports_units() -> None:
    """The planner names a Java port's libraries by the module's jar (D-92)."""
    assert CppAdapter().port_names("shop.jar", "shop.Cart.ItemFilter", ()) == (
        "shop",
        "item_filter",
    )
    assert facades.type_names("java:shop.Cart,java:shop.Cart$ItemFilter") == (
        "shop.Cart, shop.Cart.ItemFilter"
    )


def test_the_cmake_files_build_the_jni_glue() -> None:
    top = javaports.top_cmake("shop", "pom.xml", "cpp17")
    assert top.startswith(javaports.TOP_MARK)
    assert "project(shop LANGUAGES C CXX)" in top
    assert "option(CODECOREMORPH_SANITIZE" in top
    cpp = javaports.cpp_cmake("shop", "pom.xml", ["src/money.cpp"], ["bridge/money.cpp"])
    assert "add_library(shop_cpp STATIC" in cpp
    assert "add_library(shop_native SHARED" in cpp
    assert "  bridge/codecoremorph_jni.c\n  bridge/codecoremorph_jni.g.c\n" in cpp
    assert 'find_path(CCM_JNI_INCLUDE jni.h HINTS "$ENV{JAVA_HOME}/include"' in cpp
    assert "${CCM_JNI_MD_INCLUDE}" in cpp
    assert javaports.listed(cpp) == ["src/money.cpp"]
    assert javaports.listed(cpp, bridge=True) == ["bridge/money.cpp"]
    assert javaports.native_name(cpp) == "shop_native"


def test_the_first_unit_writes_the_build_the_runtime_and_the_support(tmp_path: Path) -> None:
    root = _project(tmp_path)
    changed = javaports.scaffold(root, "cpp-java-type", OPTIONS, [], "cpp17")
    assert javaports.is_port(root)
    for rel in javaports.RUNTIME_FILES.values():
        assert (root / rel).is_file(), rel
    assert (root / javaports.GLUE).is_file()
    interop = root / "src/main/java/codecoremorph/interop"
    for name in facades.FACADE_FILES:
        assert (interop / name).is_file(), name
    native = (interop / "CcmNative.java").read_text(encoding="utf-8")
    assert 'public static final String LIBRARY = "shop_native";' in native
    for rel in ("cpp/src/money.hpp", "cpp/src/money.cpp", "cpp/bridge/money.h"):
        assert rel in changed
    cpp = (root / javaports.CPP_CMAKE).read_text(encoding="utf-8")
    assert javaports.listed(cpp) == ["src/money.cpp"]
    assert javaports.listed(cpp, bridge=True) == ["bridge/money.cpp"]
    # The runtime's and the generated files are the runtime's: no unit's own C++.
    assert javaports.bridge_headers(root) == ["cpp/bridge/money.h"]
    assert "cpp/bridge/codecoremorph_jni.g.c" not in javaports.port_sources(root)
    assert facades.facade_dirs(root) == ["src/main/java/codecoremorph/interop"]
    # A second unit adds its files and keeps the first's.
    cart = {**OPTIONS, "module": "cart", "types": "java:shop.Cart"}
    javaports.scaffold(root, "cpp-java-type", cart, [], "cpp17")
    cpp = (root / javaports.CPP_CMAKE).read_text(encoding="utf-8")
    assert javaports.listed(cpp) == ["src/cart.cpp", "src/money.cpp"]


def test_the_support_goes_in_the_modules_own_sources(tmp_path: Path) -> None:
    pom = "<project><build><sourceDirectory>java</sourceDirectory></build></project>"
    assert facades.main_source_root("core/pom.xml", pom) == "core/java"
    assert facades.main_source_root("pom.xml", "<project/>") == "src/main/java"


@pytest.mark.parametrize(
    "options",
    [
        {**OPTIONS, "crate": "Shop!"},
        {**OPTIONS, "build_target": "missing/pom.xml"},
        {**OPTIONS, "build_target": "src/Shop.csproj"},
    ],
)
def test_bad_options_are_refused(tmp_path: Path, options: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        javaports.scaffold(_project(tmp_path), "cpp-java-type", options, [], "cpp17")


def test_a_test_file_becomes_a_googletest_program(tmp_path: Path) -> None:
    root = _project(tmp_path)
    with pytest.raises(ConfigError, match="after the types"):
        javaports.scaffold(root, "cpp-java-tests", {"crate": "shop", "module": "cart_test"}, [], "")
    javaports.scaffold(root, "cpp-java-type", OPTIONS, [], "cpp17")
    javaports.scaffold(
        root,
        "cpp-java-tests",
        {"crate": "shop", "module": "cart_test"},
        ["src/test/java/shop/CartTest.java"],
        "cpp17",
    )
    tests_cmake = (root / javaports.TESTS_CMAKE).read_text(encoding="utf-8")
    assert "codecoremorph_gtest(cart_test)" in tests_cmake
    # The source is Java's, not C#'s, whose ports share the file's shape (D-78).
    assert "translated from Java (CodeCoreMorph, D-92)" in tests_cmake
    assert "C#" not in tests_cmake
    stub = (root / "cpp/tests/cart_test.cpp").read_text(encoding="utf-8")
    assert "src/test/java/shop/CartTest.java" in stub
    assert 'java = "junit:' in (root / javaports.TEST_MAP).read_text(encoding="utf-8")


def test_the_retirement_leaves_a_cmake_project(tmp_path: Path) -> None:
    root = _project(tmp_path)
    javaports.scaffold(root, "cpp-java-type", OPTIONS, [], "cpp17")
    (root / "mvnw").write_text("#!/bin/sh\n", encoding="utf-8")
    ci = root / ".github/workflows/build.yml"
    ci.parent.mkdir(parents=True)
    ci.write_text("steps:\n  - run: mvn -B verify\n", encoding="utf-8")
    files = ["pom.xml", "src/main/java/shop/Money.java"]
    javaports.scaffold(root, "cpp-java-retire", {}, files, "cpp17")
    assert not (root / "pom.xml").exists()
    assert not (root / "mvnw").exists()
    assert not (root / "src").exists()  # the sources and the support, and their directories
    assert not (root / javaports.BRIDGE_DIR).exists()
    cpp = (root / javaports.CPP_CMAKE).read_text(encoding="utf-8")
    assert "add_library(shop_native" not in cpp
    assert "add_library(shop_cpp STATIC" in cpp
    assert (root / "cpp/include/codecoremorph_java.hpp").is_file()  # the library keeps it
    assert facades.retirement_left(root, "the CMake build") == [
        (".github/workflows/build.yml", "a CI configuration that builds the Java: rewrite it "
         "for the CMake build")
    ]  # fmt: skip


def _as_added(root: Path) -> str:
    patch: list[str] = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file.relative_to(root).as_posix()
        lines = file.read_text(encoding="utf-8").splitlines()
        patch.append(
            f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
        )
    return "".join(patch)


def test_the_scaffolds_add_no_build_time_command(tmp_path: Path) -> None:
    """I-12: what the scaffolds write runs and downloads nothing at configure or build time."""
    root = _project(tmp_path)
    javaports.scaffold(root, "cpp-java-type", OPTIONS, [], "cpp17")
    javaports.scaffold(root, "cpp-java-tests", {"crate": "shop", "module": "t"}, [], "cpp17")
    assert check_build_commands(patch_lines(_as_added(root))).passed
    javaports.scaffold(root, "cpp-java-retire", {}, ["pom.xml"], "cpp17")
    assert check_build_commands(patch_lines(_as_added(root))).passed


def test_the_target_of_a_java_port_names_the_image_with_both_toolchains() -> None:
    lang = LanguageConfig(language="cpp", standard="cpp17", port_source="java")
    spec = CppAdapter().toolchain_requirements(lang)
    assert spec.image == "codecoremorph-java-port"
    assert spec.language == "java-port"
    assert "mvn" in {t.name for t in spec.tools}
    tools = CppAdapter().capabilities().codemod_tools
    for tool in javaports.SCAFFOLDS:
        assert tool in tools


def test_the_test_jvm_environment() -> None:
    env = javaports.test_environment("/build/lib", "/llvm/libclang_rt.asan.so")
    assert env["LD_LIBRARY_PATH"] == "/build/lib"
    assert env["LD_PRELOAD"] == "/llvm/libclang_rt.asan.so"
    assert "handle_segv=0" in env["ASAN_OPTIONS"]  # HotSpot handles SIGSEGV itself
