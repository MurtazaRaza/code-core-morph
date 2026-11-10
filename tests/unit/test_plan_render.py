"""`codecoremorph plan`'s summary: what a port's source depends on is a table of its own, and
what has to happen first a warning (D-82)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.cli import plan as plan_cli
from codecoremorph.core.models import (
    Baseline,
    IndexMeta,
    Plan,
    PortDependency,
    PortDependencyKind,
    SymbolIndex,
    TestBaseline,
)
from codecoremorph.orchestrator.planning import PlanOutcome


def test_a_ports_dependencies_are_a_table_and_its_actions_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = Plan(
        language="csharp",
        source_standard="csharp9",
        target_standard="cpp17",
        target_language="cpp",
        dependencies=[
            PortDependency(
                kind=PortDependencyKind.NATIVE_LIBRARY,
                name="libsodium",
                version="1.0.18.2",
                ported=False,
                summary="libsodium (a native library, through P/Invoke): the image lacks it",
                action="build an image with libsodium-dev and set sandbox.image to it",
            ),
            PortDependency(
                kind=PortDependencyKind.PLATFORM_API,
                name="kernel32.dll",
                ported=True,
                summary="nothing calls it, so it goes with the retirement",
            ),
        ],
        warnings=["libsodium: build an image with libsodium-dev and set sandbox.image to it"],
    )
    outcome = PlanOutcome(
        plan=plan,
        baseline=Baseline(
            base_commit="0" * 40,
            standard="csharp9",
            tests=TestBaseline(runs=1, passing=["dotnet:T#A"]),
            configure_s=1,
            build_s=1,
            test_s=1,
        ),
        index=SymbolIndex(
            path=tmp_path / "index.sqlite",
            meta=IndexMeta(adapter="csharp", adapter_version="1"),
        ),
        duration_estimate_s=60,
        plan_path=tmp_path / "plan.json",
        baseline_path=tmp_path / "baseline.json",
    )
    plan_cli.render_plan(outcome, "20261110-000000-abcd", "libsodium-core")
    out = " ".join(capsys.readouterr().out.split())  # the table wraps in a narrow console
    assert "What the port depends on" in out
    assert "libsodium 1.0.18.2" in out
    assert "native library" in out
    assert "platform api" in out
    assert "the image lacks it" in out
    assert "warning: libsodium: build an image with libsodium-dev" in out
