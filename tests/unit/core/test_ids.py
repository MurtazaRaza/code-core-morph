from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from codecoremorph.core.ids import (
    is_run_id,
    new_run_id,
    run_branch,
    unit_id,
    validate_run_id,
    validate_unit_id,
)


def test_run_id_format() -> None:
    now = datetime(2026, 11, 2, 14, 15, 0, tzinfo=UTC)
    assert new_run_id(now, suffix="a3f9") == "20261102-141500-a3f9"


def test_run_id_uses_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    now = datetime(2026, 11, 2, 16, 15, 0, tzinfo=plus_two)
    assert new_run_id(now, suffix="0000") == "20261102-141500-0000"


def test_run_id_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        new_run_id(datetime(2026, 11, 2, 14, 15))


def test_random_suffixes_are_well_formed() -> None:
    ids = {new_run_id() for _ in range(50)}
    assert all(is_run_id(i) for i in ids)


@given(st.datetimes(timezones=st.just(UTC), min_value=datetime(2000, 1, 1)))
def test_any_aware_time_gives_a_valid_id(now: datetime) -> None:
    assert is_run_id(new_run_id(now))


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "20261102-141500",
        "20261102-141500-A3F9",
        "20261102-141500-a3f9x",
        "../20261102-141500-a3f9",
        "20261102-141500-a3f9/..",
        "2026112-141500-a3f9",
    ],
)
def test_validate_run_id_rejects(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid run ID"):
        validate_run_id(bad)


def test_run_branch() -> None:
    assert run_branch("20261102-141500-a3f9") == "codecoremorph/20261102-141500-a3f9"
    with pytest.raises(ValueError, match="invalid run ID"):
        run_branch("main")


def test_unit_ids() -> None:
    assert unit_id(42) == "U-0042"
    assert unit_id(12345) == "U-12345"
    assert validate_unit_id("U-0001") == "U-0001"
    with pytest.raises(ValueError, match="start at 1"):
        unit_id(0)
    for bad in ("U-1", "u-0001", "U-0001/..", "X-0001"):
        with pytest.raises(ValueError, match="invalid unit ID"):
            validate_unit_id(bad)
