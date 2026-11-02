"""Plan the benchmark migration with ``codecoremorph plan`` and record a summary (Phase 3 exit).

Usage: uv run python bench/plan.py [--project DIR] [--sandbox native|docker]

Writes a manifest for the pinned project (bench/fetch.py) next to its clone, migrating it
C++11 -> C++20 with the rules of examples/cpp11-to-cpp20.yaml, runs the plan (baseline, index,
graph, units; no code changes), and records units, estimates and timings in bench/results/.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.core.paths import codecoremorph_home

BENCH = Path(__file__).resolve().parent
RULES = [
    "replace_typedefs_with_using",
    "convert_raw_pointers_to_smart_pointers",
    "use_concepts_for_templates",
]


def manifest(pin: dict[str, object], root: Path, sandbox: str) -> str:
    def items(values: object) -> str:
        return ", ".join(json.dumps(v) for v in values) if isinstance(values, list) else ""

    return f"""\
version: "1.0"
project:
  name: {pin["project"]}
  root: {json.dumps(root.name)}
  test_dirs: [{items(pin["test_dirs"])}]
  exclude: [{items(pin["exclude"])}]
  public_api: [{items(pin["public_api"])}]
migration:
  source: {{language: cpp, standard: cpp11, build_system: cmake}}
  target: {{language: cpp, standard: cpp20, rules: [{", ".join(RULES)}]}}
build:
  cmake:
    configure_args: [{items(pin["configure_args"])}]
guardrails: {{build_timeout_s: 3600, test_timeout_s: 3600}}
sandbox: {{mode: {sandbox}}}
"""


def main(argv: list[str]) -> int:
    pin = json.loads((BENCH / "pin.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Plan the benchmark migration.")
    parser.add_argument(
        "--project", type=Path, default=codecoremorph_home() / "bench" / pin["project"]
    )
    parser.add_argument("--sandbox", choices=("native", "docker"), default="docker")
    args = parser.parse_args(argv)
    root: Path = args.project.resolve()
    if not (root / ".git").exists():
        print(f"{root} doesn't exist; run bench/fetch.py first", file=sys.stderr)
        return 1
    config = root.parent / f"{pin['project']}-plan.yaml"
    config.write_text(manifest(pin, root, args.sandbox), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "codecoremorph", "plan", str(config), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    payload = json.loads(result.stdout)
    plan = payload["plan"]
    tests = payload["baseline"]["tests"]
    statuses: dict[str, int] = {}
    for unit in plan["units"]:
        statuses[unit["status"]] = statuses.get(unit["status"], 0) + 1
    record = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": f"{pin['project']} {pin['tag']} ({pin['commit'][:12]})",
        "migration": "cpp11 -> cpp20",
        "sandbox": args.sandbox,
        "machine": {
            "os": platform.platform(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "timings": payload["timings"],
        "baseline": {
            "passing": len(tests["passing"]),
            "failing": tests["failing"],
            "flaky": tests["flaky"],
            "build_s": payload["baseline"]["build_s"],
            "test_s": payload["baseline"]["test_s"],
        },
        "graph": plan["graph"],
        "rules": plan["rules"],
        "units": statuses,
        "estimate": plan["estimate"],
        "duration_estimate_s": payload["duration_estimate_s"],
        "warnings": plan["warnings"],
    }
    results = BENCH / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    out = results / f"{stamp}-{pin['project']}-plan-{system}-{args.sandbox}.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    print(f"\nwrote {out}\nplan: {payload['plan_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
