"""Migrate the benchmark project end to end with Claude Code as the agent (Phase 5, M1).

Usage: uv run python bench/run.py [--project DIR] [--sandbox docker|native] [--claude PATH]
                                  [--claude-model MODEL] [--max-turns N] [--timeout SECONDS]

Headless Claude Code (``claude -p``), with the codecoremorph skill installed in the pinned
project (bench/fetch.py), migrates it C++11 -> C++20 with the rules of
examples/cpp11-to-cpp20.yaml through ``codecoremorph run --host-agent`` (D-44). It runs on the
Claude plan Claude Code is logged in with: no API key and no per-token bill, but a whole run
uses a good part of the plan's limits. The migration happens on the run's own branch; the
project's checkout isn't changed.

The run's status, every unit's outcome, the final verification, the time taken and Claude
Code's usage go to bench/results/<date>-m1-<project>-<system>-<sandbox>-claude-code.json.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plan import manifest

from codecoremorph.cli.skill import install_skill
from codecoremorph.core.models import RunReport, RunStatus
from codecoremorph.core.paths import codecoremorph_home
from codecoremorph.orchestrator.lifecycle import RunPlace, abort_run
from codecoremorph.prompts import PROMPTS_VERSION

BENCH = Path(__file__).resolve().parent
CLAUDE_TOOLS = ["Skill", "Bash(codecoremorph:*)", "Read", "Write", "Glob", "Grep"]
CLAUDE_PROMPT = """\
Use the codecoremorph skill to migrate this C++ project from C++11 to C++20: the whole \
migration, every unit of the plan, in one run.

The manifest is {manifest}, outside this directory, and the codecoremorph command is on the \
PATH; pass `-c "{manifest}"` to every codecoremorph command. Work on your own without asking \
questions: I have asked for the whole migration, so approve the plan yourself. Stop when the \
run has ended, then summarize its report.
"""


def _claude_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # a nested session must not think it runs inside this one
    scripts = Path(sys.executable).parent  # the venv's Scripts (or bin): `codecoremorph`
    env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"
    return env


def _git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def _claude(args: argparse.Namespace, root: Path, config: Path) -> dict[str, Any]:
    prompt = CLAUDE_PROMPT.format(manifest=config.as_posix())
    argv = [args.claude, "-p", prompt, "--output-format", "json",
            "--max-turns", str(args.max_turns), "--allowedTools", *CLAUDE_TOOLS]  # fmt: skip
    if args.claude_model:
        argv += ["--model", args.claude_model]
    try:
        done = subprocess.run(
            argv, cwd=root, env=_claude_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.timeout, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {args.timeout} s"}
    try:
        reply: dict[str, Any] = json.loads(done.stdout)
    except json.JSONDecodeError:
        reply = {"is_error": True, "result": (done.stdout + done.stderr)[-3000:]}
    return {
        "exit_code": done.returncode,
        "is_error": reply.get("is_error"),
        "subtype": reply.get("subtype"),
        "num_turns": reply.get("num_turns"),
        "duration_ms": reply.get("duration_ms"),
        "total_cost_usd_equivalent": reply.get("total_cost_usd"),
        "usage": reply.get("usage"),
        "result": str(reply.get("result", ""))[-6000:],
    }


def _runs_before(place: RunPlace) -> set[str]:
    with place.store() as store:
        return {r.id for r in store.list_runs()}


def _summary(place: RunPlace, run_id: str) -> dict[str, Any]:
    """The run's report, as the benchmark records it."""
    path = place.state_dir.run(run_id).report_json
    if not path.is_file():
        return {"report": None}
    report = RunReport.model_validate_json(path.read_bytes())
    units = [
        {
            "id": u.unit_id,
            "status": u.status.value,
            "attempts": u.attempts,
            "agent_skipped": u.agent_skipped,
            "duration_s": u.duration_s,
            "changed_files": len(u.changed_files),
            "reason": u.reason,
        }
        for u in report.units
    ]
    ran = [u for u in report.units if not u.agent_skipped]
    committed = report.unit_counts.get("committed", 0)
    planned = sum(n for s, n in report.unit_counts.items() if s not in ("skipped", "too_large"))
    return {
        "status": report.status.value,
        "reason": report.reason,
        "unit_counts": report.unit_counts,
        "success_rate": round(committed / planned, 3) if planned else None,
        "agent_units": len(ran),
        "attempts_per_agent_unit": round(sum(u.attempts for u in ran) / len(ran), 2)
        if ran
        else None,
        "final": report.final.model_dump(mode="json") if report.final else None,
        "rules": [r.model_dump(mode="json") for r in report.rules],
        "review_items": len(report.review),
        "duration_s": report.duration_s,
        "units": units,
        "report": str(path.with_suffix(".md")),
    }


def main(argv: list[str]) -> int:
    pin = json.loads((BENCH / "pin.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Run the benchmark migration with Claude Code.")
    parser.add_argument(
        "--project", type=Path, default=codecoremorph_home() / "bench" / pin["project"]
    )
    parser.add_argument("--sandbox", choices=("native", "docker"), default="docker")
    parser.add_argument("--claude", default=shutil.which("claude"), help="Claude Code binary")
    parser.add_argument("--claude-model", default=None, help="Claude Code: --model")
    parser.add_argument("--max-turns", type=int, default=3000, help="Claude Code: turn limit")
    parser.add_argument("--timeout", type=int, default=12 * 3600, help="seconds for the run")
    args = parser.parse_args(argv)
    if args.claude is None:
        parser.error("Claude Code isn't on the PATH; pass its binary with --claude")
    root: Path = args.project.resolve()
    if not (root / ".git").exists():
        print(f"{root} doesn't exist; run bench/fetch.py first", file=sys.stderr)
        return 1
    config = root.parent / f"{pin['project']}-run.yaml"  # beside the clone, as bench/plan.py's
    config.write_text(manifest(pin, root, args.sandbox), encoding="utf-8")
    install_skill(root / ".claude", force=True)
    exclude = root / ".git" / "info" / "exclude"
    known = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if "/.claude/" not in known.splitlines():
        exclude.write_text(known + "/.claude/\n", encoding="utf-8")
    head = _git(root, "rev-parse", "HEAD").strip()

    place = RunPlace.for_config(config)
    before = _runs_before(place) if place.state_dir.db_path.is_file() else set()
    started = time.monotonic()
    claude = _claude(args, root, config)
    seconds = round(time.monotonic() - started, 1)
    with place.store() as store:
        new = [r for r in store.list_runs() if r.id not in before]
    run = new[-1] if new else None
    ended_by_harness = False
    if run is not None and not run.status.is_terminal:
        # Claude Code stopped before the run ended: end it, so the report says how far it got.
        abort_run(config, run.id, reason="the benchmark harness ended the run")
        ended_by_harness = True
    record: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": f"{pin['project']} {pin['tag']} ({pin['commit'][:12]})",
        "migration": "cpp11 -> cpp20",
        "agent": f"claude-code/{args.claude_model or 'default'}",
        "sandbox": args.sandbox,
        "prompts": PROMPTS_VERSION,
        "machine": {
            "os": platform.platform(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "base_commit": head,
        "seconds": seconds,
        "runs_started": len(new),
        "run_id": run.id if run else None,
        "ended_by_harness": ended_by_harness,
        "user_tree_clean": not _git(root, "status", "--porcelain").strip(),
        "run": _summary(place, run.id) if run else {"status": RunStatus.FAILED.value,
                                                    "reason": "no run was started"},
        "claude": claude,
    }  # fmt: skip
    results = BENCH / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    out = results / f"{stamp}-m1-{pin['project']}-{system}-{args.sandbox}-claude-code.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k not in ("claude",)}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
