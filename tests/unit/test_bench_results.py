"""bench/run.py keeps every run's results: a second run on the same day gets a file of its own
(the M11 benchmark's second run would have written over the first's), and a resumed run writes
over its own."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

BENCH = Path(__file__).resolve().parents[2] / "bench"


@pytest.fixture
def bench_run(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    monkeypatch.syspath_prepend(str(BENCH))  # run.py imports its neighbours by name
    known = set(sys.modules)
    spec = importlib.util.spec_from_file_location("bench_run", BENCH / "run.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in ("plan", "work"):
        if name not in known:
            sys.modules.pop(name, None)


def _results(path: Path, run_id: str) -> None:
    path.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")


def test_a_second_run_on_the_same_day_keeps_the_first_runs_results(
    bench_run: ModuleType, tmp_path: Path
) -> None:
    out = tmp_path / "20261110-m11-libsodium-core-windows-docker-claude-code.json"
    assert bench_run.results_path(out, "R1") == out  # nothing there yet
    _results(out, "R1")
    second = bench_run.results_path(out, "R2")
    assert second.name == "20261110-m11-libsodium-core-windows-docker-claude-code-2.json"
    _results(second, "R2")
    assert bench_run.results_path(out, "R3").name.endswith("-claude-code-3.json")
    # A run that started none has no ID, and takes a file of its own too.
    assert bench_run.results_path(out, None).name.endswith("-claude-code-3.json")


def test_a_resumed_run_writes_over_its_own_results(bench_run: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "20261110-m11-x-windows-docker-claude-code.json"
    _results(out, "R1")
    _results(out.with_name(f"{out.stem}-2.json"), "R2")
    assert bench_run.results_path(out, "R1") == out
    assert bench_run.results_path(out, "R2") == out.with_name(f"{out.stem}-2.json")
