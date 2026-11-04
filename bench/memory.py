"""The engine's memory over a whole benchmark run (NFR-1.2: at most 4 GB resident).

Usage: uv run python bench/memory.py [--project DIR] [--sandbox native|docker]

Runs the M1 migration of the pinned benchmark project (bench/pin.json, cloned by
bench/fetch.py) in this process, with every rule of examples/cpp11-to-cpp20.yaml: the baseline
built and tested twice, the index, the plan, every unit's codemods, build, tests and
invariants, and the final verification from a clean build. The agent is a scripted model that
gives up at once, so units the codemods can't finish fail fast and no model is called; the
work the engine itself does is the same as in a run with a real agent. The engine's peak
resident memory (``engine_peak_rss_mb`` in the run's environment) goes to
bench/results/<date>-memory-<project>-<system>-<sandbox>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plan import manifest
from run import _summary

from codecoremorph.config import load_config
from codecoremorph.config.models import SandboxMode
from codecoremorph.core.models import CompletionRequest, CompletionResult, StopReason, TextBlock
from codecoremorph.core.paths import codecoremorph_home
from codecoremorph.core.resources import MIB, peak_rss_bytes
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import ScriptedProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.lifecycle import RunPlace, start_run
from codecoremorph.orchestrator.run import PEAK_MEMORY_KEY, RunOptions

BENCH = Path(__file__).resolve().parent
LIMIT_MIB = 4 * 1024  # NFR-1.2


def give_up(request: CompletionRequest) -> CompletionResult:
    """A model that declines every unit, in plain text, as the system prompt allows."""
    return CompletionResult(
        provider="anthropic",
        model=request.model,
        content=[TextBlock(text="I can't complete this unit in the memory benchmark.")],
        stop_reason=StopReason.END,
    )


def main(argv: list[str]) -> int:
    pin = json.loads((BENCH / "pin.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Measure the engine's memory over a run.")
    parser.add_argument(
        "--project", type=Path, default=codecoremorph_home() / "bench" / pin["project"]
    )
    parser.add_argument("--sandbox", choices=("native", "docker"), default="native")
    args = parser.parse_args(argv)
    root: Path = args.project.resolve()
    if not (root / ".git").exists():
        print(f"{root} doesn't exist; run bench/fetch.py first", file=sys.stderr)
        return 1
    config = root.parent / f"{pin['project']}-memory.yaml"
    text = manifest(pin, root, args.sandbox) + (
        "approvals: {plan: auto}\nparity: {characterization_tests: {enabled: false}}\n"
    )
    config.write_text(text, encoding="utf-8")

    scripted = ScriptedProvider([give_up] * 10_000)

    def factory(ledger: UsageLedger) -> LLMService:
        llm = load_config(config).config.llm
        return LLMService(llm, ledger=ledger, providers={"anthropic": scripted})

    started = time.monotonic()
    result = start_run(
        config,
        base=None,
        mode=SandboxMode(args.sandbox),
        options=RunOptions(yes=True),
        llm_factory=factory,
    )
    seconds = round(time.monotonic() - started, 1)
    place = RunPlace.for_config(config)
    with place.store() as store:
        environment = store.run_environment(result.run_id)
    peak = environment.get(PEAK_MEMORY_KEY)
    final_peak = round((peak_rss_bytes() or 0) / MIB)
    record: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": f"{pin['project']} {pin['tag']} ({pin['commit'][:12]})",
        "migration": "cpp11 -> cpp20",
        "sandbox": args.sandbox,
        "machine": {
            "os": platform.platform(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "seconds": seconds,
        "run_id": result.run_id,
        "engine_peak_rss_mib": peak,
        "process_peak_rss_mib_at_exit": final_peak,
        "limit_mib": LIMIT_MIB,
        "within_limit": isinstance(peak, int | float) and peak <= LIMIT_MIB,
        "model_calls": len(scripted.requests),
        "run": _summary(place, result.run_id),
    }
    results = BENCH / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    out = results / f"{stamp}-memory-{pin['project']}-{system}-{args.sandbox}.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "run"}, indent=2))
    print(f"\nwrote {out}")
    return 0 if record["within_limit"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
