"""Fetch the pinned benchmark project (SPEC §12, D-10).

Usage: uv run python bench/fetch.py [--pin FILE] [DEST]

Clones the pin's repository (default bench/pin.json, M1's leveldb; bench/port-pin.json is M8's
snappy) at its pinned commit, with submodules, into DEST (default:
<CODECOREMORPH_HOME>/bench/<project>), then checks that every commit matches the pin. Git
verifies content by commit hash, so the pin is the checksum. An existing clone at the right
commits is left alone.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from codecoremorph.core.paths import codecoremorph_home

PIN = Path(__file__).resolve().with_name("pin.json")


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def verify(dest: Path, pin: dict[str, object]) -> list[str]:
    problems: list[str] = []
    head = git("rev-parse", "HEAD", cwd=dest)
    if head != pin["commit"]:
        problems.append(f"HEAD is {head}, expected {pin['commit']}")
    submodules = pin.get("submodules") or {}
    if not isinstance(submodules, dict):
        raise SystemExit("the pin's submodules must be an object")
    for path, commit in submodules.items():
        actual = git("rev-parse", "HEAD", cwd=dest / str(path))
        if actual != commit:
            problems.append(f"{path} is at {actual}, expected {commit}")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Fetch the pinned benchmark project.")
    parser.add_argument("--pin", type=Path, default=PIN, help="the benchmark's pin")
    parser.add_argument("dest", type=Path, nargs="?", help="where to clone it")
    args = parser.parse_args(argv)
    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    dest: Path = args.dest or codecoremorph_home() / "bench" / str(pin["project"])
    if (dest / ".git").exists():
        problems = verify(dest, pin)
        if not problems:
            print(f"{dest} is already at {pin['project']} {pin['tag']} ({pin['commit'][:12]})")
            return 0
        print("existing clone doesn't match the pin:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"cloning {pin['url']} {pin['tag']} into {dest}")
    git(
        "clone",
        "--quiet",
        "--branch",
        str(pin["tag"]),
        "--depth",
        "1",
        "--recurse-submodules",
        "--shallow-submodules",
        str(pin["url"]),
        str(dest),
    )
    problems = verify(dest, pin)
    if problems:
        print("the clone doesn't match the pin:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    print(f"ok: {pin['project']} {pin['tag']} at {pin['commit']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
