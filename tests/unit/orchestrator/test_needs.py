"""A port's unit runs only after the units it needs committed (D-60)."""

from __future__ import annotations

from types import SimpleNamespace

from codecoremorph.core.models import UnitStatus, WorkUnit
from codecoremorph.orchestrator.run import RunEngine


def _unit(n: int, status: UnitStatus, needs: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        id=f"U-{n:04d}",
        seq=n,
        rules=["port_tests"],
        files=[f"tests/t{n}.cpp"],
        status=status,
        needs=list(needs),
    )


class _Store:
    def __init__(self, units: list[WorkUnit]):
        self._units = units

    def units(self, run_id: str) -> list[WorkUnit]:
        return list(self._units)


def _unmet(units: list[WorkUnit], unit: WorkUnit) -> str | None:
    engine = SimpleNamespace(store=_Store(units), run_id="20261105-000000-abcd")
    return RunEngine._unmet_needs(engine, unit)  # type: ignore[arg-type]  # a stand-in engine


def test_a_unit_waits_for_what_it_needs() -> None:
    committed = _unit(1, UnitStatus.COMMITTED)
    failed = _unit(2, UnitStatus.FAILED)
    too_large = _unit(3, UnitStatus.TOO_LARGE)
    later = _unit(4, UnitStatus.PENDING, ("U-0001",))
    assert _unmet([committed, later], later) is None
    blocked = _unit(5, UnitStatus.PENDING, ("U-0001", "U-0002", "U-0003"))
    assert _unmet([committed, failed, too_large, blocked], blocked) == (
        "needs U-0002 (failed), U-0003 (too_large), which didn't commit"
    )
    # A unit without needs, and needs the plan no longer has (an excluded run), don't block.
    assert _unmet([committed], _unit(6, UnitStatus.PENDING)) is None
    assert _unmet([committed], _unit(7, UnitStatus.PENDING, ("U-0099",))) is None
