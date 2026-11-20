"""The Rust side of a Java → Rust port (SPEC §11.12, D-92): the workspace with CodeCoreMorph's
Java runtime crate, the library's crate with its bridge and JNI glue, the Java support in the
façade module, and the tests and retirement scaffolds."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.java import facades
from codecoremorph.adapters.rust import RustAdapter, javaports
from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import LanguageConfig
from codecoremorph.invariants.checks import check_build_commands, patch_lines

OPTIONS = {
    "crate": "shop",
    "build_target": "pom.xml",
    "module": "money",
    "types": "java:shop.Money",
}


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "src/main/java/shop").mkdir(parents=True)
    (root / "pom.xml").write_text("<project><artifactId>shop</artifactId></project>\n")
    (root / "src/main/java/shop/Money.java").write_text("package shop;\n", encoding="utf-8")
    return root


def test_the_rust_adapter_names_a_java_ports_units() -> None:
    assert RustAdapter().port_names("shop.jar", "shop.Cart", ()) == ("shop", "cart")
    assert RustAdapter().port_names("shop.jar", "shop.Type", ()) == ("shop", "type_mod")


def test_the_first_unit_writes_the_workspace_the_crate_and_the_support(tmp_path: Path) -> None:
    root = _project(tmp_path)
    javaports.scaffold(root, "rust-java-type", OPTIONS, [], "rust2021")
    assert javaports.is_port(root)
    workspace = (root / "rust/Cargo.toml").read_text(encoding="utf-8")
    assert 'members = ["codecoremorph_java", "shop_rs"]' in workspace
    for rel in javaports.RUNTIME_FILES:
        assert (root / "rust/codecoremorph_java" / rel).is_file(), rel
    crate = root / "rust/shop_rs"
    manifest = (crate / "Cargo.toml").read_text(encoding="utf-8")
    assert 'crate-type = ["cdylib", "rlib"]' in manifest
    assert 'codecoremorph_java = { path = "../codecoremorph_java" }' in manifest
    assert 'java-module = "pom.xml"' in manifest
    lib = (crate / "src/lib.rs").read_text(encoding="utf-8")
    assert "#![deny(unsafe_code)]" in lib
    assert "pub mod money;" in lib
    bridge = (crate / "src/bridge/mod.rs").read_text(encoding="utf-8")
    assert "mod money;" in bridge
    # The JNI modules are include!d, so cbindgen never takes them for the bridge's.
    for name in ("jni_sys.rs", "jni.rs", "jni_g.rs"):
        assert f'include!("{name}");' in bridge
        assert (crate / "src/bridge" / name).is_file()
    assert "cbindgen:ignore" in bridge
    assert (crate / "src/bridge/ccm.rs").is_file()
    assert (crate / "include/shop_rs/codecoremorph_bridge.h").is_file()
    cbindgen = (crate / "cbindgen.toml").read_text(encoding="utf-8")
    assert 'includes = ["codecoremorph_bridge.h"]' in cbindgen
    native = (root / "src/main/java/codecoremorph/interop/CcmNative.java").read_text(
        encoding="utf-8"
    )
    assert 'public static final String LIBRARY = "shop_rs";' in native
    assert javaports.facade_library(root) == ("rust/shop_rs", "shop_rs")
    assert facades.facade_dirs(root) == ["src/main/java/codecoremorph/interop"]


def test_tests_come_after_the_types(tmp_path: Path) -> None:
    root = _project(tmp_path)
    tests = {"crate": "shop", "module": "cart_test"}
    with pytest.raises(ConfigError, match="after the types"):
        javaports.scaffold(root, "rust-java-tests", tests, [], "rust2021")
    javaports.scaffold(root, "rust-java-type", OPTIONS, [], "rust2021")
    files = ["src/test/java/shop/CartTest.java"]
    javaports.scaffold(root, "rust-java-tests", tests, files, "rust2021")
    stub = (root / "rust/shop_rs/tests/cart_test.rs").read_text(encoding="utf-8")
    assert "src/test/java/shop/CartTest.java" in stub
    assert 'java = "junit:' in (root / javaports.TEST_MAP).read_text(encoding="utf-8")


def test_the_retirement_leaves_a_cargo_workspace(tmp_path: Path) -> None:
    root = _project(tmp_path)
    javaports.scaffold(root, "rust-java-type", OPTIONS, [], "rust2021")
    javaports.scaffold(
        root, "rust-java-retire", {}, ["pom.xml", "src/main/java/shop/Money.java"], "rust2021"
    )
    crate = root / "rust/shop_rs"
    assert not (root / "pom.xml").exists()
    assert not (root / "src").exists()
    assert not (crate / "src/bridge").exists()
    assert not (crate / "include").exists()
    assert not (crate / "cbindgen.toml").exists()
    lib = (crate / "src/lib.rs").read_text(encoding="utf-8")
    assert "#![forbid(unsafe_code)]" in lib
    assert "mod bridge" not in lib
    assert lib.startswith("//! The Java library `pom.xml`, ported to Rust")
    manifest = (crate / "Cargo.toml").read_text(encoding="utf-8")
    assert "cdylib" not in manifest
    assert "java-module" not in manifest
    assert (root / "rust/codecoremorph_java/src/lang.rs").is_file()  # the runtime stays


def test_the_scaffolds_add_no_build_time_command(tmp_path: Path) -> None:
    """I-12: no build script, nothing run or downloaded at build time."""
    root = _project(tmp_path)
    javaports.scaffold(root, "rust-java-type", OPTIONS, [], "rust2021")
    patch = "".join(
        f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
        + "".join(f"+{line}\n" for line in p.read_text(encoding="utf-8").splitlines())
        for p in sorted(root.rglob("*"))
        if p.is_file()
        for rel in [p.relative_to(root).as_posix()]
    )
    assert check_build_commands(patch_lines(patch)).passed
    assert not list(root.rglob("build.rs"))


def test_the_target_of_a_java_port_names_the_image_with_both_toolchains() -> None:
    lang = LanguageConfig(language="rust", standard="rust2021", port_source="java")
    spec = RustAdapter().toolchain_requirements(lang)
    assert spec.image == "codecoremorph-java-port"
    assert {"mvn", "bindgen", "cbindgen"} <= {t.name for t in spec.tools}
    for tool in javaports.SCAFFOLDS:
        assert tool in RustAdapter().capabilities().codemod_tools


def test_bindgen_reads_the_jdks_jni_header() -> None:
    argv = javaports.jni_bindgen_argv("/jdk/include/jni.h", "/jdk/include", "/jdk/include/linux",
                                      "/w/jni_sys.rs")  # fmt: skip
    assert argv[:2] == ["bindgen", "/jdk/include/jni.h"]
    assert "-I/jdk/include/linux" in argv
    assert argv[argv.index("-o") + 1] == "/w/jni_sys.rs"
