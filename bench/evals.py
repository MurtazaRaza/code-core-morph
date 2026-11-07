"""Run the eval set against a live agent and record the results (Phase 4, SPEC §12).

Usage:
  uv run python bench/evals.py --agent claude-code [--claude PATH] [--claude-model MODEL]
  uv run python bench/evals.py --provider anthropic [--model claude-opus-5-5] [--effort high]
  uv run python bench/evals.py --provider ollama --model qwen3:4b --num-ctx 32768
  options: --cases ID... (default: every case) --sandbox native|docker --record DIR

Two kinds of agent:

- ``--agent claude-code`` (D-41): headless Claude Code (``claude -p``) with the codecoremorph
  skill installed in the case's project, driving ``codecoremorph unit`` itself. It runs on
  the Claude plan Claude Code is logged in with: no API key and no per-token bill, but each
  case uses some of the plan's usage limits.
- ``--provider``: CodeCoreMorph's own agent loop calls a model provider. Paid providers cost
  money: every case is one unit with up to four attempts. ``--record DIR`` also writes each
  case's model calls to a cassette, so a run can be replayed later.

Each case copies its fixture into a fresh repository under a temporary directory, runs one
unit, and records pass or fail, attempts and time (plus tokens and cost for providers).
Results go to bench/results/<date>-evals-<agent>-<model>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from codecoremorph.agent.host import HostFiles
from codecoremorph.cli.skill import install_skill
from codecoremorph.core.errors import CodeCoreMorphError
from codecoremorph.core.models import HostStatus, HostUnitState
from codecoremorph.llm.base import LLMProvider, ProviderRegistry
from codecoremorph.llm.ledger import UsageLedger
from codecoremorph.llm.replay import Cassette, RecordingProvider
from codecoremorph.llm.service import LLMService
from codecoremorph.orchestrator.session import open_session
from codecoremorph.orchestrator.unit import HostRun, abort_host_unit, run_single_unit
from codecoremorph.prompts import PROMPTS_VERSION
from codecoremorph.store.layout import StateDir

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
FIXTURES = ROOT / "fixtures" / "cpp"
CLAUDE_TOOLS = ["Skill", "Bash(codecoremorph:*)", "Read", "Write", "Edit", "Glob", "Grep"]
CLAUDE_PROMPT = """\
Use the codecoremorph skill to migrate one work unit of this C++ project from {source} to \
{target}.

- Rules: {rules}
- Files: {files}

The manifest is migration_config.yaml in this directory, and the codecoremorph command is on \
the PATH. Work on your own without asking questions. Stop when the unit has succeeded, failed \
or been aborted, and then summarize the outcome.
"""


def manifest(case: dict[str, Any], args: argparse.Namespace) -> str:
    source, target = case["migration"]
    data: dict[str, Any] = {
        "version": "1.0",
        "project": {
            "name": case["id"],
            "source_dirs": ["src", "include", "app"],
            "test_dirs": ["tests"],
            "public_api": ["include/**"],
        },
        "migration": {
            "source": {"language": "cpp", "standard": source, "build_system": "cmake"},
            "target": {"language": "cpp", "standard": target, "rules": []},
        },
        "guardrails": {"max_run_cost_usd": args.max_cost},
        "parity": {"baseline_runs": 1},
        "sandbox": {"mode": args.sandbox},
    }
    if args.agent == "api":
        role: dict[str, Any] = {"provider": args.provider, "model": args.model}
        if args.effort:
            role["effort"] = args.effort
        if args.num_ctx:
            role["num_ctx"] = args.num_ctx
        data["llm"] = {"roles": {"editor": role}}
    return yaml.safe_dump(data, sort_keys=False)


def git(cwd: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=evals",
         "-c", "user.email=evals@codecoremorph.invalid", *argv],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


def setup_case(case: dict[str, Any], args: argparse.Namespace, work: Path) -> Path:
    root = work / case["id"]
    shutil.copytree(FIXTURES / case["fixture"], root)
    (root / "migration_config.yaml").write_text(manifest(case, args), encoding="utf-8")
    git(root, "init", "--quiet")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "legacy")
    return root


def run_api_case(case: dict[str, Any], args: argparse.Namespace, root: Path) -> dict[str, Any]:
    config = root / "migration_config.yaml"
    ledger = UsageLedger(max_cost_usd=args.max_cost)
    registry = ProviderRegistry.discover()
    record: dict[str, Any] = {}
    try:
        with open_session(config) as session:
            provider: LLMProvider = registry.create(args.provider, session.config.llm)
            if args.record is not None:
                cassette = Cassette(Path(args.record) / f"{case['id']}.json")
                provider = RecordingProvider(provider, cassette)
            service = LLMService(
                session.config.llm, ledger=ledger, providers={args.provider: provider}
            )
            try:
                result = run_single_unit(
                    session, rule_ids=list(case["rules"]), files=list(case["files"]), llm=service
                )
            finally:
                service.close()
        outcome = result.outcome
        record |= {
            "passed": outcome.succeeded,
            "agent_skipped": outcome.agent_skipped,
            "attempts": len(outcome.attempts),
            "attempt_outcomes": [a.outcome.value if a.outcome else None for a in outcome.attempts],
            "reason": outcome.reason,
            "summary": outcome.summary,
            "review_notes": outcome.review_notes,
            "warnings": outcome.warnings,
            "changed_files": outcome.changed_files,
        }
    except CodeCoreMorphError as exc:
        record |= {"passed": False, "error": f"{type(exc).__name__}: {exc.message}"}
    usage = ledger.total.usage
    record |= {
        "llm_calls": ledger.total.calls,
        "tokens": usage.model_dump(),
        "cost_usd": round(ledger.total.cost_usd, 4),
    }
    return record


def _claude_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)  # a nested session must not think it runs inside this one
    scripts = Path(sys.executable).parent  # the venv's Scripts (or bin): `codecoremorph`
    env["PATH"] = f"{scripts}{os.pathsep}{env.get('PATH', '')}"
    return env


def _host_states(root: Path) -> list[HostUnitState]:
    runs = StateDir.for_repo(root).runs_dir
    if not runs.is_dir():
        return []
    states = [HostFiles(p).load() for p in runs.iterdir() if HostFiles(p).state.is_file()]
    return sorted(states, key=lambda s: s.started_at)


def run_claude_case(case: dict[str, Any], args: argparse.Namespace, root: Path) -> dict[str, Any]:
    install_skill(root / ".claude", force=True)
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "/.claude/\n", encoding="utf-8")
    source, target = case["migration"]
    prompt = CLAUDE_PROMPT.format(
        source=source, target=target, rules=", ".join(case["rules"]), files=", ".join(case["files"])
    )
    argv = [args.claude, "-p", prompt, "--output-format", "json",
            "--max-turns", str(args.max_turns), "--allowedTools", *CLAUDE_TOOLS]  # fmt: skip
    if args.claude_model:
        argv += ["--model", args.claude_model]
    record: dict[str, Any] = {}
    try:
        done = subprocess.run(
            argv, cwd=root, env=_claude_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.case_timeout, check=False,
        )  # fmt: skip
        try:
            reply: dict[str, Any] = json.loads(done.stdout)
        except json.JSONDecodeError:
            reply = {"is_error": True, "result": (done.stdout + done.stderr)[-2000:]}
        record |= {
            "claude": {
                "exit_code": done.returncode,
                "is_error": reply.get("is_error"),
                "subtype": reply.get("subtype"),
                "num_turns": reply.get("num_turns"),
                "duration_ms": reply.get("duration_ms"),
                "total_cost_usd_equivalent": reply.get("total_cost_usd"),
                "usage": reply.get("usage"),
                "result": str(reply.get("result", ""))[-3000:],
            }
        }
    except subprocess.TimeoutExpired:
        record["claude"] = {"error": f"timed out after {args.case_timeout} s"}
    states = _host_states(root)
    run = HostRun.for_config(root / "migration_config.yaml")
    if states and states[-1].status is HostStatus.WAITING:
        abort_host_unit(run, "the eval ended with the unit still waiting")
        states = _host_states(root)
    last = states[-1] if states else None
    status = git(root, "status", "--porcelain").strip()
    record |= {
        "passed": last is not None and last.status is HostStatus.SUCCEEDED,
        "units_started": len(states),
        "status": last.status.value if last else "not_started",
        "agent_skipped": last.agent_skipped if last else None,
        "attempts": len(last.attempts) if last else 0,
        "attempt_outcomes": [a.outcome.value if a.outcome else None for a in last.attempts]
        if last
        else [],
        "reason": last.reason if last else "Claude Code never started a unit",
        "summary": last.summary if last else None,
        "review_notes": last.review_notes if last else [],
        "warnings": last.warnings if last else [],
        "changed_files": last.changed_files if last else [],
        "diff": git(root, "diff", last.base_commit, last.commit) if last and last.commit else "",
        "user_tree_clean": not status,
        "cost_usd": 0.0,
    }
    return record


def run_case(case: dict[str, Any], args: argparse.Namespace, work: Path) -> dict[str, Any]:
    started = time.monotonic()
    record: dict[str, Any] = {"id": case["id"], "rules": case["rules"], "files": case["files"]}
    root = setup_case(case, args, work)
    if args.agent == "claude-code":
        record |= run_claude_case(case, args, root)
    else:
        record |= run_api_case(case, args, root)
    record["seconds"] = round(time.monotonic() - started, 1)
    return record


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run the eval set against a live agent.")
    parser.add_argument("--agent", choices=("api", "claude-code"), default="api")
    parser.add_argument("--provider", choices=("anthropic", "ollama"), default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=None, help="Anthropic: reasoning effort")
    parser.add_argument("--num-ctx", type=int, default=None, help="Ollama: context window")
    parser.add_argument("--claude", default=shutil.which("claude"), help="Claude Code binary")
    parser.add_argument("--claude-model", default=None, help="Claude Code: --model")
    parser.add_argument("--max-turns", type=int, default=80, help="Claude Code: turn limit")
    parser.add_argument("--case-timeout", type=int, default=3600, help="Claude Code: seconds")
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--sandbox", choices=("native", "docker"), default="native")
    parser.add_argument("--max-cost", type=float, default=5.0, help="USD cap per case")
    parser.add_argument("--record", default=None, help="Write cassettes to this directory")
    parser.add_argument("--set", default=str(BENCH / "evals" / "cpp.yaml"))
    args = parser.parse_args(argv)
    if args.agent == "api":
        if args.provider is None:
            parser.error("--provider is required with --agent api")
        if args.model is None:
            args.model = "claude-opus-5-5" if args.provider == "anthropic" else "qwen3:4b"
        if args.provider == "anthropic" and args.effort is None:
            args.effort = "high"
        if args.provider == "ollama" and args.num_ctx is None:
            args.num_ctx = 32768
    elif args.claude is None:
        parser.error("Claude Code isn't on the PATH; pass its binary with --claude")
    cases: list[dict[str, Any]] = yaml.safe_load(Path(args.set).read_text(encoding="utf-8"))[
        "cases"
    ]
    if args.cases:
        unknown = sorted(set(args.cases) - {c["id"] for c in cases})
        if unknown:
            print(f"unknown cases: {', '.join(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in args.cases]

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ccm-evals-", ignore_cleanup_errors=True) as tmp:
        for case in cases:
            print(f"--- {case['id']}", file=sys.stderr, flush=True)
            result = run_case(case, args, Path(tmp))
            results.append(result)
            print(
                f"{'PASS' if result['passed'] else 'FAIL'} {case['id']}: "
                f"{result.get('attempts', 0)} attempts, ${result.get('cost_usd', 0):.2f}, "
                f"{result['seconds']} s {result.get('reason') or result.get('error') or ''}",
                file=sys.stderr,
                flush=True,
            )
    passed = sum(1 for r in results if r["passed"])
    if args.agent == "claude-code":
        agent, model = "claude-code", args.claude_model or "default"
    else:
        agent, model = args.provider, args.model
    record = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent": args.agent,
        "model": f"{agent}/{model}",
        "options": {"effort": args.effort, "num_ctx": args.num_ctx},
        "sandbox": args.sandbox,
        "prompts": PROMPTS_VERSION,
        "machine": {"os": platform.platform(), "cpus": os.cpu_count()},
        "cases": len(results),
        "passed": passed,
        "success_rate": round(passed / len(results), 3) if results else None,
        "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in results), 4),
        "seconds": round(sum(r["seconds"] for r in results), 1),
        "results": results,
    }
    out_dir = BENCH / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    safe_model = model.replace(":", "-").replace("/", "-")
    out = out_dir / f"{stamp}-evals-{agent}-{safe_model}.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in record.items() if k != "results"}, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
