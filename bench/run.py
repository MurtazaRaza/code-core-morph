"""Migrate the benchmark project end to end with Claude Code as the agent (M1, M8, M9).

Usage: uv run python bench/run.py [--pin FILE] [--project DIR] [--sandbox docker|native]
                                  [--claude PATH] [--claude-model MODEL] [--max-turns N]
                                  [--timeout SECONDS] [--resume RUN_ID]

Headless Claude Code (``claude -p``), with the codecoremorph skill installed in the pinned
project (bench/fetch.py), runs the pin's migration through ``codecoremorph run --host-agent``
(D-44): bench/pin.json (M1) migrates leveldb C++11 -> C++20 with the rules of
examples/cpp11-to-cpp20.yaml, bench/port-pin.json (M8) ports snappy whole to Rust and
bench/c-port-pin.json (M9) ports bzip2, C with a Makefile, whole to Rust. It runs on
the Claude plan Claude Code is logged in with: no API key and no per-token bill, but a whole
run uses a good part of the plan's limits. The migration happens on the run's own branch; the
project's checkout isn't changed.

The run's status, every unit's outcome, the final verification, a port's summary (maps,
deviations, unsafe code, C or C++ left), the time taken against the work the plan held
(bench/work.py) and Claude Code's usage go to
bench/results/<date>-<milestone>-<project>-<system>-<sandbox>-claude-code.json.

When Claude Code stops before the run ends, the harness aborts the run, so the report says how
far it got, unless the Claude plan's usage limit stopped it: then the run stays parked, and
``--resume RUN_ID`` continues it with a new session once the limit resets. The results then
list every session and add up their time.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from plan import PIN, describe, load_pin, manifest, migration, source
from work import measure

from codecoremorph.cli.skill import install_skill
from codecoremorph.core.models import RunReport, RunStatus
from codecoremorph.core.paths import codecoremorph_home
from codecoremorph.orchestrator.lifecycle import RunPlace, abort_run
from codecoremorph.prompts import PROMPTS_VERSION

BENCH = Path(__file__).resolve().parent
# Edit too: an agent that fixes its edits file in place is refused without it, and headless
# Claude Code can't ask (the M9 run's first attempt stopped there).
CLAUDE_TOOLS = ["Skill", "Bash(codecoremorph:*)", "Read", "Write", "Edit", "Glob", "Grep"]
CLAUDE_PROMPT = """\
Use the codecoremorph skill to {task}: the whole {kind}, every unit of the plan, in one run.

The manifest is {manifest}, outside this directory, and the codecoremorph command is on the \
PATH; pass it to every codecoremorph command, as the argument of `run`, `plan`, `validate` and \
`doctor` and as `-c "{manifest}"` to the others. Work on your own without asking \
questions: I have asked for the whole {kind}, so approve the plan yourself. Stop when the \
run has ended, then summarize its report.
"""
RESUME_PROMPT = """\
Use the codecoremorph skill to finish run {run_id}: the whole {kind} I asked for ({task}), \
parked where an earlier session stopped. Continue it to its end, every unit left, as the skill \
describes (`codecoremorph status`, `codecoremorph resume` and the unit commands).

The manifest is {manifest}, outside this directory, and the codecoremorph command is on the \
PATH; pass it to every codecoremorph command, as the argument of `run`, `plan`, `validate` and \
`doctor` and as `-c "{manifest}"` to the others. Work on your own without asking \
questions. Stop when the run has ended, then summarize its report.
"""
# Claude Code's reply when the Claude plan's usage limit stops it: the run stays parked.
USAGE_LIMIT = re.compile(r"hit your .{0,40}limit|usage limit", re.IGNORECASE)
LANGUAGES = {"c": "C", "cpp": "C++", "rust": "Rust"}


def _standard(name: str) -> str:
    return name.replace("cpp", "C++") if name.startswith("cpp") else name


def task(pin: dict[str, Any]) -> tuple[str, str]:
    """What Claude Code is asked to do, and what to call it: a migration or a port."""
    found = migration(pin)
    target = found["target"]
    if target["language"] == "cpp":
        return (
            f"migrate this C++ project from {_standard(found['source'])} to "
            f"{_standard(target['standard'])}",
            "migration",
        )
    language = source(pin)["language"]
    return (
        f"port this {LANGUAGES.get(language, language)} project to "
        f"{LANGUAGES.get(target['language'], target['language'])}",
        "port",
    )


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


def limited(claude: dict[str, Any]) -> bool:
    """Whether Claude Code stopped at the Claude plan's usage limit."""
    return bool(USAGE_LIMIT.search(str(claude.get("result", ""))))


def _claude(args: argparse.Namespace, root: Path, prompt: str) -> dict[str, Any]:
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
        "port": report.port.model_dump(mode="json") if report.port else None,
        "rules": [r.model_dump(mode="json") for r in report.rules],
        "review_items": len(report.review),
        "duration_s": report.duration_s,
        "units": units,
        "report": str(path.with_suffix(".md")),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the benchmark migration with Claude Code.")
    parser.add_argument("--pin", type=Path, default=PIN, help="the benchmark's pin")
    parser.add_argument("--project", type=Path, help="its clone (default: bench/fetch.py's)")
    parser.add_argument("--sandbox", choices=("native", "docker"), default="docker")
    parser.add_argument("--claude", default=shutil.which("claude"), help="Claude Code binary")
    parser.add_argument("--claude-model", default=None, help="Claude Code: --model")
    parser.add_argument("--max-turns", type=int, default=3000, help="Claude Code: turn limit")
    parser.add_argument("--timeout", type=int, default=12 * 3600, help="seconds for the run")
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="continue a run an earlier session left parked at the plan's usage limit",
    )
    args = parser.parse_args(argv)
    if args.claude is None:
        parser.error("Claude Code isn't on the PATH; pass its binary with --claude")
    pin = load_pin(args.pin)
    if args.project is None:
        args.project = codecoremorph_home() / "bench" / pin["project"]
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
    results = BENCH / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    milestone = pin.get("milestone", "m1")
    out = results / f"{stamp}-{milestone}-{pin['project']}-{system}-{args.sandbox}-claude-code.json"

    place = RunPlace.for_config(config)
    what, kind = task(pin)
    earlier: dict[str, Any] | None = None
    if args.resume:
        with place.store() as store:
            parked = store.get_run(args.resume)
        if parked is None or parked.status.is_terminal:
            state = "doesn't exist" if parked is None else f"has ended ({parked.status.value})"
            print(f"run {args.resume} {state}: nothing to resume", file=sys.stderr)
            return 1
        if out.is_file():
            found = json.loads(out.read_text(encoding="utf-8"))
            earlier = found if found.get("run_id") == args.resume else None
        prompt = RESUME_PROMPT.format(
            run_id=args.resume, task=what, kind=kind, manifest=config.as_posix()
        )
    else:
        prompt = CLAUDE_PROMPT.format(task=what, kind=kind, manifest=config.as_posix())
    before = _runs_before(place) if place.state_dir.db_path.is_file() else set()
    if args.resume:
        before.discard(args.resume)
    started = time.monotonic()
    claude = _claude(args, root, prompt)
    seconds = round(time.monotonic() - started, 1)
    with place.store() as store:
        new = [r for r in store.list_runs() if r.id not in before]
    run = new[-1] if new else None
    ended_by_harness = False
    paused = False
    if run is not None and not run.status.is_terminal:
        if limited(claude):
            # The plan's usage limit stopped Claude Code: the run stays parked, for --resume.
            paused = True
        else:
            # Claude Code stopped before the run ended: end it, so the report says how far it
            # got.
            abort_run(config, run.id, reason="the benchmark harness ended the run")
            ended_by_harness = True
    sessions = [*(earlier or {}).get("claude_sessions", []), claude]
    if earlier is not None:
        seconds = round(seconds + float(earlier.get("seconds", 0)), 1)
        head = str(earlier.get("base_commit", head))
    record: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": f"{pin['project']} {pin['tag']} ({pin['commit'][:12]})",
        "migration": describe(pin),
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
        "paused_at_usage_limit": paused,
        "user_tree_clean": not _git(root, "status", "--porcelain").strip(),
        "run": _summary(place, run.id) if run else {"status": RunStatus.FAILED.value,
                                                    "reason": "no run was started"},
        "work": measure(place, run.id, root, list(pin["exclude"])) if run else None,
        "claude": claude,
        "claude_sessions": sessions,
    }  # fmt: skip
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    shown = ("claude", "claude_sessions", "work")
    print(json.dumps({k: v for k, v in record.items() if k not in shown}, indent=2))
    print(f"\nwrote {out}")
    if paused and run is not None:
        print(
            f"\nThe Claude plan's usage limit stopped Claude Code; run {run.id} is parked. When "
            f"the limit resets: bench/run.py --pin {args.pin} --sandbox {args.sandbox} "
            f"--resume {run.id}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
