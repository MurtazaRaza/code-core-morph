"""Whole Java → Rust ports with the real toolchains (Phase 20, M14, D-91, D-92): each Java
fixture's port, every unit included, from the manifest to the report.

The agent's edits come from ``tests/cassettes/java/edits/<fixture>-rust.json``, written from a
host-agent run (as D-45 did for C++): a scripted model submits each unit's edits, and its calls
are recorded (``pytest --record-cassettes``) and replayed like the others
(``run-all-<fixture>-rust-docker.json``). The run uses the image for ports from Java, which holds
the Rust toolchain, the JDKs and Maven, so these runs need ``codecoremorph-java-port:local`` and
are marked ``docker``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from .test_java_to_cpp_e2e import _edits_files, run_port  # pyright: ignore[reportPrivateUsage]

GitRunner = Callable[..., str]

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging")]


@pytest.mark.slow
@pytest.mark.parametrize("edits", _edits_files("-rust"))
def test_every_unit_of_a_java_port_to_rust(
    edits: str, tmp_path: Path, record_cassettes: bool, git: GitRunner
) -> None:
    """The fixture's port to Rust: every unit committed as recorded, in plan order, the user's
    checkout untouched, the Java tests passing through the façades until the retirement, and
    the final verification a clean Cargo build, --locked and offline, whose tests, each a
    translated Java test, pass. No Java source, POM or façade support is left on the branch, nor
    the bridge, its header, its JNI glue or the cdylib, and no unsafe code; the Rust,
    CodeCoreMorph's runtime crate and both maps are. With ``keep_source_api`` (an edits file's
    ``kept`` POMs), the library stays a Java library over the Rust, its façades calling the
    bridge, and the final verification runs both builds, the Java tests too. Nothing under
    orchestrator/ knows Java or Rust (NFR-3.1)."""
    data, result, report, root = run_port(edits, tmp_path, record_cassettes, git)
    tracked = git(root, "ls-tree", "-r", "--name-only", result.branch).split()
    crates = {f.split("/")[1] for f in tracked if f.startswith("rust/") and f.count("/") > 2}
    library = next(c for c in sorted(crates) if c != "codecoremorph_java")
    if data.get("kept"):
        # The POMs, the façades and their support, and the bridge with its JNI glue under them,
        # in the cdylib the façades load.
        assert sorted(f for f in tracked if f.endswith("pom.xml")) == data["kept"]
        assert any(f.endswith("/codecoremorph/interop/CcmNative.java") for f in tracked)
        assert any(f.startswith(f"rust/{library}/src/bridge/") for f in tracked)
        assert "cdylib" in git(root, "show", f"{result.branch}:rust/{library}/Cargo.toml")
    else:
        # The branch is Rust: no Java, no Maven, no bridge, no cdylib, no unsafe code.
        assert not [f for f in tracked if f.endswith((".java", "pom.xml"))]
        assert not [f for f in tracked if "/src/bridge/" in f or "/include/" in f]
        assert "cdylib" not in git(root, "show", f"{result.branch}:rust/{library}/Cargo.toml")
        lib = git(root, "show", f"{result.branch}:rust/{library}/src/lib.rs")
        assert "#![forbid(unsafe_code)]" in lib
        assert report.port is not None
        assert report.port.unsafe == []
    # The Cargo workspace, CodeCoreMorph's runtime and the maps of where each Java member and
    # test went.
    assert {
        "rust/Cargo.toml",
        "rust/Cargo.lock",
        "rust/codecoremorph_java/src/exception.rs",
        "rust/codecoremorph_java/src/lang.rs",
        "rust/symbol-map.toml",
        "rust/test-map.toml",
    } <= set(tracked)
