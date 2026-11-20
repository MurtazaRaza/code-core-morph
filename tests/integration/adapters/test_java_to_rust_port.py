"""A Java → Rust port with the real toolchains (D-92), on the Phase 20 spike's ``shop`` library:
the scaffold of the unit that ports ``shop.Money``, a hand port (the Rust, its bridge, and the
same Java façade a port to C++ has), the project's own JUnit tests through the façade and the
crate's ``cdylib``, the Java declarations and the Rust JNI glue CodeCoreMorph writes from
cbindgen's header, and a planted overflow (Java's wrapping ``+`` written as Rust's) that a debug
build panics on, which the bridge turns into the failure of the test that hit it. Marked
``docker``: the run uses the image for ports from Java (``scripts/build-image java-port``)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.java import JavaAdapter
from codecoremorph.adapters.rust import RustAdapter, javaports
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, TestStatus

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
SHOP = ROOT / "tests" / "unit" / "adapters" / "java" / "data" / "shop"
DATA = Path(__file__).with_name("data")
MONEY = "src/main/java/shop/Money.java"
CRATE = "rust/shop_rs"
SCAFFOLD = CodemodSpec(
    tool="rust-java-type",
    options={
        "crate": "shop",
        "build_target": "pom.xml",
        "module": "money",
        "types": "java:shop.Money",
    },
)


@dataclass
class Contexts:
    java: AdapterContext
    rust: AdapterContext


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[Contexts]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "shop"
    shutil.copytree(SHOP, work)
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    java_lang = LanguageConfig(
        language="java",
        standard="java8",
        port_target="rust",
        build_system="maven",
        test_dirs=["**/src/test"],
        build_timeout_s=900,
        test_timeout_s=600,
    )
    rust_lang = LanguageConfig(
        language="rust",
        standard="rust2021",
        target_standard="rust2021",
        port_source="java",
        build_system="cargo",
        build_timeout_s=900,
        test_timeout_s=300,
    )
    spec = RustAdapter().toolchain_requirements(rust_lang)
    assert spec.image == "codecoremorph-java-port"
    try:
        image = resolve_image(spec, None)
    except SandboxError as exc:
        pytest.skip(f"no image for ports from Java: {exc}")
    executor = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/java-port.env",
        )
    )
    executor.start()
    try:
        java = AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=java_lang,
        )
        rust = replace(
            java,
            cache_dir=tmp_path / "cache" / "rust",
            log_dir=tmp_path / "logs" / "rust",
            lang=rust_lang,
        )
        yield Contexts(java, rust)
    finally:
        executor.stop()


def _verify(java: JavaAdapter, rust: RustAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    """One verification as a port run makes it: the target configures, then the source
    configures, builds (the crate's cdylib first) and tests."""
    configured = rust.configure(ctx.rust)
    assert configured.success, configured.diagnostics
    configured = java.configure(ctx.java)
    assert configured.success, configured.diagnostics
    built = java.build(ctx.java)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in java.run_tests(ctx.java).cases}


def test_a_java_type_ported_to_rust_by_hand(ctx: Contexts) -> None:
    java, rust = JavaAdapter(), RustAdapter()
    work = ctx.java.worktree
    baseline = _verify(java, rust, ctx)
    assert len(baseline) == 27
    assert set(baseline.values()) == {TestStatus.PASSED}

    files = [MONEY]
    scaffold = rust.run_codemods(ctx.rust, [SCAFFOLD], files)
    assert scaffold.diagnostics == []
    changed = set(scaffold.changed_files)
    assert {
        "rust/Cargo.toml",
        "rust/Cargo.lock",
        f"{CRATE}/Cargo.toml",
        f"{CRATE}/src/lib.rs",
        f"{CRATE}/src/money.rs",
        f"{CRATE}/src/bridge/money.rs",
        f"{CRATE}/src/bridge/jni_g.rs",
        "src/main/java/codecoremorph/interop/CcmNative.java",
    } <= changed
    assert javaports.is_port(work)
    # The unit's first verification: the empty bridge builds, and nothing changed yet.
    assert _verify(java, rust, ctx) == baseline
    assert len((work / javaports.sys_path(CRATE)).read_text(encoding="utf-8")) > 1000

    # The port: the Rust, its bridge, and Money a façade over it (the C++ port's façade).
    shutil.copyfile(DATA / "java_to_rust" / "money.rs", work / f"{CRATE}/src/money.rs")
    bridge = work / f"{CRATE}/src/bridge/money.rs"
    # With exports of the scalars Money doesn't pass, so the glue of each compiles.
    bridge.write_bytes(
        (DATA / "java_to_rust" / "bridge_money.rs").read_bytes()
        + (DATA / "java_to_rust" / "bridge_scalars.rs").read_bytes()
    )
    shutil.copyfile(DATA / "java_to_cpp" / "Money.java", work / MONEY)
    assert _verify(java, rust, ctx) == baseline  # every test, through the façade
    declarations = (work / "src/main/java/codecoremorph/interop/CcmNative.java").read_text(
        encoding="utf-8"
    )
    assert 'public static final String LIBRARY = "shop_rs";' in declarations
    assert "public static int shop_Money_plus(long cents, String currency," in declarations
    glue = (work / javaports.glue_path(CRATE)).read_text(encoding="utf-8")
    assert "fn Java_codecoremorph_interop_CcmNative_shop_1Money_1plus_1raw(" in glue
    assert "b: jbyte, s: jshort, f: jfloat, d: jdouble, z: jboolean" in glue
    assert "let mut c_result_d: f64 = 0.0;" in glue
    assert "jni::boolean_elements(env, flags)" in glue
    assert "jvalue { s: s as jshort }, jvalue { f: f as jfloat }" in glue
    assert "public interface shop_Scalars_each_fn {" in declarations
    assert rust.unsafe_sites(ctx.rust) == []  # unsafe code only in the bridge

    # Java's wrapping + written as Rust's: the debug build panics, the bridge turns the panic
    # into an exception, and the test that overflows fails.
    source = work / f"{CRATE}/src/money.rs"
    text = source.read_text(encoding="utf-8")
    planted = text.replace("self.cents.wrapping_add(other.cents)", "self.cents + other.cents")
    assert planted != text
    source.write_text(planted, encoding="utf-8")
    after = _verify(java, rust, ctx)
    failed = {t for t, s in after.items() if s is not TestStatus.PASSED}
    assert failed == {"junit:shop.MoneyTest#overflowWraps"}
