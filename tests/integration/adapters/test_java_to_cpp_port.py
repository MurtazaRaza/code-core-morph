"""A Java → C++ port with the real toolchains (D-92), on the Phase 20 spike's ``shop`` library:
the scaffold of the unit that ports ``shop.Money``, a hand port (the C++, its bridge, and the
Java class a façade over it), the project's own JUnit tests through the façade with the C++
under AddressSanitizer and UndefinedBehaviorSanitizer inside Surefire's JVM, the Java
declarations and the JNI glue CodeCoreMorph writes from the bridge header, and a planted signed
overflow (Java's wrapping ``+`` written as C++'s) that ends the test run with the sanitizer's
report. Marked ``docker``: the run uses the image for ports from Java
(``scripts/build-image java-port``)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter, javaports
from codecoremorph.adapters.java import JavaAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import CodemodSpec, LanguageConfig, Symbol, TestStatus
from codecoremorph.invariants.checks import api_changes, api_symbols
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
SHOP = ROOT / "tests" / "unit" / "adapters" / "java" / "data" / "shop"
PORT = Path(__file__).with_name("data") / "java_to_cpp"
MONEY = "src/main/java/shop/Money.java"
SCAFFOLD = CodemodSpec(
    tool="cpp-java-type",
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
    cpp: AdapterContext


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
        port_target="cpp",
        build_system="maven",
        test_dirs=["**/src/test"],
        build_timeout_s=900,
        test_timeout_s=600,
    )
    cpp_lang = LanguageConfig(
        language="cpp",
        standard="cpp17",
        target_standard="cpp17",
        port_source="java",
        build_system="cmake",
        build_timeout_s=900,
        test_timeout_s=300,
    )
    spec = CppAdapter().toolchain_requirements(cpp_lang)
    assert spec.image == "codecoremorph-java-port"  # the C++ of a Java port needs Java too
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
        cpp = replace(
            java,
            cache_dir=tmp_path / "cache" / "cpp",
            log_dir=tmp_path / "logs" / "cpp",
            lang=cpp_lang,
        )
        yield Contexts(java, cpp)
    finally:
        executor.stop()


def _verify(java: JavaAdapter, cpp: CppAdapter, ctx: Contexts) -> dict[str, TestStatus]:
    """One verification as a port run makes it: the target configures, then the source
    configures, builds (the bridge library first) and tests."""
    configured = cpp.configure(ctx.cpp)
    assert configured.success, configured.diagnostics
    configured = java.configure(ctx.java)
    assert configured.success, configured.diagnostics
    built = java.build(ctx.java)
    assert built.success, built.diagnostics
    return {t.test_id: t.status for t in java.run_tests(ctx.java).cases}


def _api(java: JavaAdapter, ctx: Contexts) -> list[Symbol]:
    """Money's API symbols in a port's index: its public and protected members (D-92)."""
    with IndexStore.open(java.index(ctx.java).path) as store:
        return [s for s in api_symbols(store.symbols()) if s.file == MONEY]


def test_a_java_type_ported_to_cpp_by_hand(ctx: Contexts) -> None:
    java, cpp = JavaAdapter(), CppAdapter()
    work = ctx.java.worktree
    baseline = _verify(java, cpp, ctx)
    assert len(baseline) == 27
    assert set(baseline.values()) == {TestStatus.PASSED}
    api = _api(java, ctx)  # Money's API, which its façade keeps (I-6)
    assert any("Money plus(shop.Money)" in (s.signature or "") for s in api)

    # The scaffold of the unit that ports shop.Money: the CMake build, the runtime and JNI
    # glue, the Java support in the module's sources, and the unit's C++ and bridge files.
    files = [MONEY]
    scaffold = cpp.run_codemods(ctx.cpp, [SCAFFOLD], files)
    interop = "src/main/java/codecoremorph/interop"
    assert set(scaffold.changed_files) == {
        "CMakeLists.txt",
        "cpp/CMakeLists.txt",
        *javaports.RUNTIME_FILES.values(),
        javaports.GLUE,
        "cpp/bridge/money.h",
        "cpp/bridge/money.cpp",
        "cpp/src/money.hpp",
        "cpp/src/money.cpp",
        "cpp/symbol-map.toml",
        f"{interop}/CcmInterop.java",
        f"{interop}/CcmExceptions.java",
        f"{interop}/CcmNative.java",
    }
    assert javaports.is_port(work)
    assert not cpp.run_codemods(ctx.cpp, [SCAFFOLD], files).changed_files
    # The unit's first verification: the empty bridge links, and nothing changed yet.
    assert _verify(java, cpp, ctx) == baseline

    # The port: the C++, its bridge, and Money a façade over it.
    shutil.copyfile(PORT / "money.hpp", work / "cpp/src/money.hpp")
    shutil.copyfile(PORT / "money.cpp", work / "cpp/src/money.cpp")
    shutil.copyfile(PORT / "bridge_money.h", work / "cpp/bridge/money.h")
    shutil.copyfile(PORT / "bridge_money.cpp", work / "cpp/bridge/money.cpp")
    shutil.copyfile(PORT / "Money.java", work / MONEY)
    assert _verify(java, cpp, ctx) == baseline  # every test, through the façade
    declarations = (work / interop / "CcmNative.java").read_text(encoding="utf-8")
    assert 'public static final String LIBRARY = "shop_native";' in declarations
    assert "private static native int shop_Money_plus_raw(" in declarations  # UTF-8 bytes
    assert "public static int shop_Money_plus(long cents, String currency," in declarations
    glue = (work / javaports.GLUE).read_text(encoding="utf-8")
    assert "Java_codecoremorph_interop_CcmNative_shop_1Money_1plus_1raw(" in glue
    # I-6 compares the façade with the Java by signature: nothing public changed.
    changes = api_changes(api, _api(java, ctx), files=files)
    assert changes == [], [(c.before, c.after) for c in changes]
    # The C++ lints clean: no warning, clang-tidy's checks quiet, no owning raw pointer.
    assert cpp.lint(ctx.cpp, ["cpp/src/money.cpp", "cpp/bridge/money.cpp"]) == []

    # Java's wrapping + written as C++'s: UndefinedBehaviorSanitizer aborts the forked JVM, and
    # its report is the run's failure.
    source = work / "cpp/src/money.cpp"
    text = source.read_text(encoding="utf-8")
    planted = text.replace(
        "return {java::add(cents_, other.cents_), currency_};",
        "return {cents_ + other.cents_, currency_};",
    )
    assert planted != text
    source.write_text(planted, encoding="utf-8")
    report = java.run_tests(ctx.java) if java.build(ctx.java).success else None
    assert report is not None
    (crash,) = [c for c in report.cases if c.test_id == "junit:(run)"]
    assert crash.status is TestStatus.ERROR
    assert "signed integer overflow" in (crash.message or "")
    assert "aborted the forked test JVM" in (crash.message or "")
