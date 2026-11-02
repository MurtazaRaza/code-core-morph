"""Measure indexing plus graph generation on the benchmark project (NFR-1.1) and record it.

Usage: uv run python bench/measure.py [--project DIR] [--sandbox native|docker] [--jobs N]

Configures the pinned project (bench/fetch.py) with the C++ adapter, then indexes it twice:
cold (empty per-translation-unit cache) and warm (everything cached), and builds the planner's
dependency graph from the cold index. Writes a JSON record to bench/results/ and prints it,
including a linear extrapolation of cold indexing plus graph building to 100k lines.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from codecoremorph.adapters.base import AdapterContext
from codecoremorph.adapters.cpp import CppAdapter
from codecoremorph.core.ids import new_run_id
from codecoremorph.core.models import LanguageConfig, SymbolIndex
from codecoremorph.core.paths import HomeLayout, codecoremorph_home
from codecoremorph.planner.project import ProjectModel
from codecoremorph.sandbox.executor import Executor

BENCH = Path(__file__).resolve().parent
NFR_LINES = 100_000
NFR_SECONDS = 60.0
CODE_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hpp")


def count_lines(root: Path, exclude: list[str]) -> int:
    from codecoremorph.core.globs import PathMatcher

    matcher = PathMatcher(exclude)
    total = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.suffix in CODE_SUFFIXES and ".git" not in path.parts and not matcher.matches(rel):
            total += len(path.read_bytes().splitlines())
    return total


def make_executor(kind: str, root: Path, work: Path) -> Executor:
    if kind == "native":
        from codecoremorph.sandbox.native import NativeExecutor

        return NativeExecutor(
            log_dir=work / "exec", toolchain_env=HomeLayout.resolve().toolchain_env("cpp")
        )
    from codecoremorph.sandbox.docker.executor import DockerExecutor, DockerSettings
    from codecoremorph.sandbox.provisioning import resolve_image

    spec = CppAdapter().toolchain_requirements(LanguageConfig(language="cpp", standard="cpp11"))
    return DockerExecutor(
        DockerSettings(
            image=resolve_image(spec, None),
            run_id=new_run_id(),
            worktree=root,
            build_dir=work / "build",
            cache_dir=work / "cache",
            log_dir=work / "exec",
            cpus=float(os.cpu_count() or 4),
            memory="8g",
            toolchain_env="/opt/codecoremorph/toolchains/cpp.env",
        )
    )


def main(argv: list[str]) -> int:
    pin = json.loads((BENCH / "pin.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Measure indexing time on the benchmark.")
    parser.add_argument(
        "--project", type=Path, default=codecoremorph_home() / "bench" / pin["project"]
    )
    parser.add_argument("--sandbox", choices=("native", "docker"), default="native")
    parser.add_argument("--jobs", type=int, default=None)
    args = parser.parse_args(argv)
    root: Path = args.project.resolve()
    if not (root / ".git").exists():
        print(f"{root} doesn't exist; run bench/fetch.py first", file=sys.stderr)
        return 1

    work = codecoremorph_home() / "bench" / f"work-{args.sandbox}"
    shutil.rmtree(work, ignore_errors=True)
    for name in ("build", "cache", "logs", "exec"):
        (work / name).mkdir(parents=True)
    executor = make_executor(args.sandbox, root, work)
    executor.start()
    ctx = AdapterContext(
        worktree=root,
        build_dir=work / "build",
        cache_dir=work / "cache",
        log_dir=work / "logs",
        executor=executor,
        lang=LanguageConfig(
            language="cpp",
            standard="cpp11",
            configure_args=list(pin["configure_args"]),
            exclude=list(pin["exclude"]),
            test_dirs=list(pin["test_dirs"]),
            public_api=list(pin["public_api"]),
            build_timeout_s=3600,
        ),
    )
    adapter = CppAdapter()
    try:
        started = time.monotonic()
        configured = adapter.configure(ctx)
        configure_s = time.monotonic() - started
        if not configured.success:
            print("configure failed:", *[d.message for d in configured.errors], sep="\n  ")
            return 1
        started = time.monotonic()
        cold = run_index(adapter, ctx, args.jobs)
        cold_s = time.monotonic() - started
        started = time.monotonic()
        project = ProjectModel.load(cold.path, root)
        graph_s = time.monotonic() - started
        started = time.monotonic()
        warm = run_index(adapter, ctx, args.jobs)
        warm_s = time.monotonic() - started
    finally:
        executor.stop()

    lines = count_lines(root, list(pin["exclude"]))
    projected = (cold_s + graph_s) * NFR_LINES / lines if lines else float("nan")
    clusters = project.graph.clusters
    record = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": f"{pin['project']} {pin['tag']} ({pin['commit'][:12]})",
        "sandbox": args.sandbox,
        "machine": {
            "os": platform.platform(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "lines_of_code": lines,
        "configure_s": round(configure_s, 2),
        "index_cold_s": round(cold_s, 2),
        "index_warm_s": round(warm_s, 2),
        "graph_s": round(graph_s, 2),
        "projected_100k_lines_s": round(projected, 1),
        "nfr_1_1_target_s": NFR_SECONDS,
        "index_stats": cold.meta.stats,
        "index_timings": cold.meta.timings,
        "warm_stats": warm.meta.stats,
        "graph": {
            "files": len(project.files),
            "edges": project.edge_count,
            "clusters": len(clusters),
            "cyclic_clusters": len(project.graph.cyclic),
            "largest_cluster": max((len(c) for c in clusters), default=0),
        },
        "libclang": cold.meta.tool_versions.get("libclang"),
        "problems": cold.problems[:20],
        "note": "projected_100k_lines_s covers cold indexing plus graph building (NFR-1.1).",
    }
    results = BENCH / "results"
    results.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    out = results / f"{stamp}-{pin['project']}-{system}-{args.sandbox}.json"
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    print(f"\nwrote {out}")
    return 0


def run_index(adapter: CppAdapter, ctx: AdapterContext, jobs: int | None) -> SymbolIndex:
    from codecoremorph.adapters.cpp.indexing import run_index as index

    return index(ctx, adapter_version=adapter.version, jobs=jobs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
