"""The crate's own procedural macros with the real toolchain (D-93): the Rust index of
``fixtures/rust/wire`` in the Rust image, where rust-analyzer's SCIP output names each site of
the macro package's derives, attribute and function-like macro, and its language server
expands each, offline, through the helper CodeCoreMorph ships. A second index reuses the
expansions. Marked ``docker``."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.rust import RustAdapter
from codecoremorph.core.errors import SandboxError
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import LanguageConfig, MacroKind
from codecoremorph.store.index import IndexStore

pytestmark = pytest.mark.docker

ROOT = Path(__file__).resolve().parents[3]
WIRE = ROOT / "fixtures" / "rust" / "wire"


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AdapterContext]:
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    work = tmp_path / "wire"
    shutil.copytree(WIRE, work)
    for name in ("build", "cache", "logs"):
        (tmp_path / name).mkdir()
    lang = LanguageConfig(
        language="rust", standard="rust2018", port_target="c", build_timeout_s=900
    )
    try:
        image = resolve_image(RustAdapter().toolchain_requirements(lang), None)
    except SandboxError as exc:
        pytest.skip(f"no Rust toolchain image: {exc}")
    executor = DockerExecutor(
        DockerSettings(
            image=image,
            run_id=new_run_id(),
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "exec",
            toolchain_env="/opt/codecoremorph/toolchains/rust.env",
        )
    )
    executor.start()
    try:
        yield AdapterContext(
            worktree=work,
            build_dir=tmp_path / "build",
            cache_dir=tmp_path / "cache",
            log_dir=tmp_path / "logs",
            executor=executor,
            lang=lang,
        )
    finally:
        executor.stop()


def test_each_site_and_its_expansion(ctx: AdapterContext) -> None:
    rust = RustAdapter()
    index = rust.index(ctx)
    assert not index.problems, index.problems
    with IndexStore.open(index.path) as store:
        targets = {t.name: t.proc_macro for t in store.build_targets()}
        sites = store.macro_sites()
    assert targets["wire_macros"] is True
    assert targets["wire"] is False
    found = {(s.file, s.line, s.macro): s for s in sites}
    assert len(found) == 11
    assert all(s.expansion for s in sites), [s for s in sites if not s.expansion]
    header = found[("src/msg.rs", 7, "Encode")]
    assert header.kind is MacroKind.DERIVE
    assert header.expansion is not None
    assert "impl ::wire::codec::Encode for Header" in header.expansion
    assert header.items == ["wire::msg::Header::encode", "wire::msg::Header::encoded_len"]
    reading = found[("src/msg.rs", 13, "Decode")]
    assert reading.expansion is not None
    assert "seen: ::core::default::Default::default()" in reading.expansion  # #[wire(skip)]
    frame = found[("src/msg.rs", 42, "throws")]
    assert frame.kind is MacroKind.ATTRIBUTE
    assert frame.expansion is not None
    assert "-> ::core::result::Result<(Header, Vec<u8>, usize), DecodeError>" in frame.expansion
    table = found[("src/crc.rs", 5, "crc_table")]
    assert table.kind is MacroKind.FUNCTION
    assert table.expansion is not None
    assert "0xC0C1u16" in table.expansion  # computed while the crate compiled
    assert found[("tests/frames.rs", 6, "Encode")].items == []
    # The agent loop indexes again before each query: the expansions come from the cache.
    key = ctx.cache_dir / "macros" / ".-expansions.key"
    assert key.is_file()
    stamp = key.stat().st_mtime_ns
    again = rust.index(ctx)
    assert not again.problems
    assert key.stat().st_mtime_ns == stamp
