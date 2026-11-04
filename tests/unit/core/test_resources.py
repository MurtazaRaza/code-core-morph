"""The engine's own peak memory (NFR-1.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from codecoremorph.core.models import RunRecord
from codecoremorph.core.resources import MIB, peak_rss_bytes
from codecoremorph.orchestrator import run as run_module
from codecoremorph.orchestrator.run import PEAK_MEMORY_KEY, record_peak_memory
from codecoremorph.store import StateDir, Store

RUN = "20261104-120000-ab12"


def test_the_peak_grows_with_what_the_process_holds() -> None:
    before = peak_rss_bytes()
    assert before is not None
    assert before > 0
    held = bytearray(64 * MIB)
    held[::4096] = b"x" * len(held[::4096])  # touch every page so it is resident
    after = peak_rss_bytes()
    assert after is not None
    assert after >= before + 48 * MIB
    del held


def test_the_run_keeps_its_largest_peak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 4, tzinfo=UTC)
    with Store.open(StateDir.for_repo(tmp_path).db_path) as store:
        store.create_run(
            RunRecord(
                id=RUN,
                created_at=now,
                updated_at=now,
                repo_root=tmp_path,
                base_commit="0" * 40,
                config_hash="a" * 64,
                codecoremorph_version="0.1.0",
            )
        )
        store.set_run_environment(RUN, {"sandbox": "docker"})
        for peak in (300, 1200, 800):  # three engine processes: a run that parked twice
            monkeypatch.setattr(run_module, "peak_rss_bytes", lambda p=peak: p * MIB)
            record_peak_memory(store, RUN)
        assert store.run_environment(RUN) == {"sandbox": "docker", PEAK_MEMORY_KEY: 1200}
