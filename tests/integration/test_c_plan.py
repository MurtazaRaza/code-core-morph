"""``codecoremorph plan`` for a C port (D-68): ``c-legacy`` to Rust, one unit per source file of
its library, ordered by the calls between them though one internal header declares them all.
Make builds run in Docker first, so the test is marked ``docker``."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codecoremorph.cli import app
from codecoremorph.core.models import Plan, UnitStatus

GitRunner = Callable[..., str]
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "c" / "c-legacy"
MANIFEST = """\
version: "1.0"
project:
  name: c-legacy
  source_dirs: [src, include, tools]
  test_dirs: [tests]
  public_api: ["include/**"]
migration:
  source: {language: c, standard: c89, build_system: make}
  target:
    language: rust
    standard: rust2021
    rules: [port_c_module, port_c_executable, port_c_tests, retire_c]
sandbox: {mode: docker}
"""

pytestmark = [pytest.mark.docker, pytest.mark.usefixtures("restore_logging")]


def test_a_c_port_plan(tmp_path: Path, git: GitRunner, isolated_home: Path) -> None:
    root = tmp_path / "c-legacy"
    shutil.copytree(FIXTURE, root)
    (root / "migration_config.yaml").write_text(MANIFEST, encoding="utf-8")
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    result = CliRunner().invoke(app, ["plan", str(root / "migration_config.yaml"), "--json"])
    assert result.exit_code == 0, result.stderr
    plan = Plan.model_validate(json.loads(result.stdout)["plan"])
    assert plan.migration == "c c89 -> rust rust2021"
    ported = [u for u in plan.units if u.port is not None]
    # By includes, wfint.h ties every file together; by calls, tok.c needs sbuf.c and conf.c,
    # and wf.c needs htab.c, sbuf.c and tok.c.
    assert [u.port.module for u in ported[:5] if u.port] == ["conf", "htab", "sbuf", "tok", "wf"]
    assert all(u.status is UnitStatus.PENDING for u in ported)
    sbuf = ported[2]
    assert sbuf.files == ["Makefile", "include/wf.h", "src/sbuf.c", "src/wfint.h"]
    assert sbuf.reference_files == ["include/wf.h", "src/wfint.h"]
    assert sbuf.port is not None
    assert (sbuf.port.crate, sbuf.port.archive) == ("wf_rs", "libwf.a")
    assert sbuf.port.exports == [
        "sbuf_init", "sbuf_append", "sbuf_putc", "sbuf_reset", "sbuf_free",
    ]  # fmt: skip
    assert sbuf.port.c_headers == ["src/wfint.h"]
    conf = ported[0]
    assert conf.port is not None
    assert set(conf.port.exports) == {
        "wf_conf_min_length", "wf_conf_fold_case",
        "wf_set_min_length", "wf_min_length", "wf_set_fold_case", "wf_fold_case",
    }  # fmt: skip
    assert conf.port.c_headers == ["include/wf.h", "src/wfint.h"]
    wf = ported[4]
    assert wf.port is not None
    assert wf.port.callers == ["tests/test_wf.c", "tools/wordfreq.c"]
    # The program: a Rust bin copied to the C program's path, after every unit of its library
    # (it links the crate, not the archive).
    program = ported[5]
    assert program.port is not None
    assert (program.port.bin, program.port.artifact) == ("wordfreq", "wordfreq")
    assert program.port.dependencies == ["wf_rs"]
    assert program.files == ["Makefile", "tools/wordfreq.c"]
    assert program.needs == sorted(u.id for u in ported[:5])
    # A unit per C test file: its TAP points are its tests, after the module it calls.
    tests = [u for u in plan.units if u.rules == ["port_c_tests"]]
    by_file = {u.files[0]: u for u in tests}
    assert set(by_file) == {f"tests/test_{m}.c" for m in ("htab", "sbuf", "tok", "wf")}
    sbuf_tests = by_file["tests/test_sbuf.c"]
    assert sbuf_tests.port is not None
    assert (sbuf_tests.port.crate, sbuf_tests.port.module) == ("wf_rs", "test_sbuf")
    assert len(sbuf_tests.port.tests) == 8
    assert all(t.startswith("tap:tests/test_sbuf/") for t in sbuf_tests.port.tests)
    assert sbuf_tests.port.wrappers == []  # a C test's entries are its cases
    assert sbuf.id in sbuf_tests.needs
    # Then the C goes, with the Make build.
    retire = plan.units[-1]
    assert retire.rules == ["retire_c"]
    assert "Makefile" in retire.files
    assert retire.note is not None
    assert retire.note.startswith("retires the C: deletes ")
    assert "and the Make build" in retire.note
    assert set(retire.needs) == {u.id for u in [*ported, *tests]}


AUTOTOOLS = Path(__file__).resolve().parents[2] / "fixtures" / "c" / "c-autotools"


def test_an_automake_port_plan(tmp_path: Path, git: GitRunner, isolated_home: Path) -> None:
    """``c-autotools`` (D-70): its libtool library's archive is libtool's, its program is the
    wrapper's path, the variadic functions stay C, and each test file is a unit of its own
    though two of them each define a ``struct sink``."""
    root = tmp_path / "c-autotools"
    shutil.copytree(AUTOTOOLS, root)
    manifest = MANIFEST.replace("c-legacy", "c-autotools").replace("c89", "c99")
    (root / "migration_config.yaml").write_text(manifest, encoding="utf-8")
    git(root.parent, "init", "--quiet", str(root))
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "kvconf")
    result = CliRunner().invoke(app, ["plan", str(root / "migration_config.yaml"), "--json"])
    assert result.exit_code == 0, result.stderr
    plan = Plan.model_validate(json.loads(result.stdout)["plan"])
    ported = [u for u in plan.units if u.port is not None and u.rules == ["port_c_module"]]
    assert [u.port.module for u in ported if u.port] == ["log", "strutil", "value", "doc", "parse"]
    log = ported[0]
    assert log.port is not None
    assert log.port.archive == "src/.libs/libkvconf.a"
    assert log.port.exports == ["kv_options_init", "kv_level_name", "kv_strerror", "kv_log_write"]
    assert log.note is not None
    assert "kv_vlog and kv_log stay C in src/log.c" in log.note
    assert {"src/Makefile.am", "configure.ac"} <= set(log.files)
    program = next(u for u in plan.units if u.rules == ["port_c_executable"])
    assert program.port is not None
    assert (program.port.bin, program.port.artifact) == ("kvdump", "tools/kvdump")
    tests = {u.files[0]: u for u in plan.units if u.rules == ["port_c_tests"]}
    assert all(len(u.files) == 1 for u in tests.values())
    assert tests["tests/test_log.c"].port is not None
    assert tests["tests/test_log.c"].port.tests == ["make:tests/test_log"]
    assert plan.units[-1].rules == ["retire_c"]
