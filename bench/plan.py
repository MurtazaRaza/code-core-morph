"""Plan the benchmark migration with ``codecoremorph plan`` and record a summary (Phase 3 exit).

Usage: uv run python bench/plan.py [--pin FILE] [--project DIR] [--sandbox native|docker]

Writes a manifest for the pinned project (bench/fetch.py) next to its clone, runs the plan
(baseline, index, graph, units; no code changes), and records units, estimates and timings in
bench/results/. The pin names the migration: bench/pin.json (M1) names none, so leveldb goes
C++11 -> C++20 with the rules of examples/cpp11-to-cpp20.yaml; bench/port-pin.json (M8) ports
snappy to Rust, bench/c-port-pin.json (M9) ports bzip2, a C library built with Make, and
bench/rust-port-pin.json (M10) ports shlex, a Rust crate, to C.
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
from typing import Any

from codecoremorph.core.paths import codecoremorph_home

BENCH = Path(__file__).resolve().parent
PIN = BENCH / "pin.json"
RULES = [
    "replace_typedefs_with_using",
    "convert_raw_pointers_to_smart_pointers",
    "use_concepts_for_templates",
]
UPGRADE: dict[str, Any] = {
    "source": "cpp11",
    "target": {"language": "cpp", "standard": "cpp20", "rules": RULES},
}


def load_pin(path: Path) -> dict[str, Any]:
    pin: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return pin


def migration(pin: dict[str, Any]) -> dict[str, Any]:
    """The pin's migration; a pin without one is M1's C++11 -> C++20 upgrade."""
    found = pin.get("migration")
    return found if isinstance(found, dict) else UPGRADE


def source(pin: dict[str, Any]) -> dict[str, str]:
    """The project's language, standard and build system; a bare standard is C++ with CMake."""
    found = migration(pin)["source"]
    if isinstance(found, dict):
        return {str(k): str(v) for k, v in found.items()}
    return {"language": "cpp", "standard": str(found), "build_system": "cmake"}


def describe(pin: dict[str, Any]) -> str:
    """The migration in a few words, as the results record it: ``cpp11 -> rust2021``."""
    return f"{source(pin)['standard']} -> {migration(pin)['target']['standard']}"


def toolchain_image(pin: dict[str, Any]) -> str | None:
    """The image a pin's ``image`` builds on top of its toolchain's, by ID, or None.

    ``image`` names a Dockerfile under bench/ (``{"dockerfile": "images/libsodium/Dockerfile",
    "toolchain": "csharp-port"}``) whose ``BASE`` argument is the toolchain image: a project's
    native libraries added for the code a port writes, which the plan says it needs (D-82).
    Docker's CLI builds it; a local image's ID pins it by content, as ``sandbox.image`` wants.
    """
    found = pin.get("image")
    if not isinstance(found, dict):
        return None
    from codecoremorph.sandbox.provisioning import read_lock

    # By its published digest, else its local build's tag: BuildKit takes no image ID in FROM.
    lock = read_lock(str(found.get("toolchain", "csharp-port")))
    base = lock.pinned_reference or lock.local_reference
    dockerfile = BENCH / str(found["dockerfile"])
    tag = f"codecoremorph-bench-{pin['project'].lower()}:local"
    subprocess.run(
        ["docker", "build", "--build-arg", f"BASE={base}", "-t", tag, "-f", str(dockerfile),
         str(dockerfile.parent)],
        check=True,
    )  # fmt: skip
    return subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def _build(pin: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """The pin's build options by build system: its ``build``, else CMake's configure_args."""
    found = pin.get("build")
    if isinstance(found, dict):
        return found
    return {"cmake": {"configure_args": list(pin.get("configure_args", []))}}


def manifest(pin: dict[str, Any], root: Path, sandbox: str, image: str | None = None) -> str:
    def items(values: object) -> str:
        return ", ".join(json.dumps(v) for v in values) if isinstance(values, list) else ""

    found = migration(pin)
    target = found["target"]
    src = source(pin)
    build = "".join(
        f"  {system}:\n" + "".join(f"    {k}: [{items(v)}]\n" for k, v in options.items())
        for system, options in _build(pin).items()
    )
    build_block = f"build:\n{build}" if build else ""  # a Cargo crate needs no options
    return f"""\
version: "1.0"
project:
  name: {pin["project"]}
  root: {json.dumps(root.name)}
  test_dirs: [{items(pin["test_dirs"])}]
  exclude: [{items(pin["exclude"])}]
  public_api: [{items(pin["public_api"])}]
migration:
  source: {{language: {src["language"]}, standard: {src["standard"]}, \
build_system: {src["build_system"]}}}
  target:
    language: {target["language"]}
    standard: {target["standard"]}
    rules: [{", ".join(target["rules"])}]
{build_block}guardrails: {{build_timeout_s: 3600, test_timeout_s: 3600}}
sandbox: {{mode: {sandbox}{f", image: {json.dumps(image)}" if image else ""}}}
"""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Plan the benchmark migration.")
    parser.add_argument("--pin", type=Path, default=PIN, help="the benchmark's pin")
    parser.add_argument("--project", type=Path, help="its clone (default: bench/fetch.py's)")
    parser.add_argument("--sandbox", choices=("native", "docker"), default="docker")
    args = parser.parse_args(argv)
    pin = load_pin(args.pin)
    if args.project is None:
        args.project = codecoremorph_home() / "bench" / pin["project"]
    root: Path = args.project.resolve()
    if not (root / ".git").exists():
        print(f"{root} doesn't exist; run bench/fetch.py first", file=sys.stderr)
        return 1
    config = root.parent / f"{pin['project']}-plan.yaml"
    image = toolchain_image(pin) if args.sandbox == "docker" else None
    config.write_text(manifest(pin, root, args.sandbox, image), encoding="utf-8")

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
        "migration": describe(pin),
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
