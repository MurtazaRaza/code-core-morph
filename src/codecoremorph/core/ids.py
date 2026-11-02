"""Run and work-unit identifiers.

Run IDs look like ``20261102-141500-a3f9`` (SPEC §3): a UTC timestamp plus four random hex
digits. They become directory names and part of the branch name ``codecoremorph/<run-id>``, so
every place that accepts one from outside validates it with :func:`validate_run_id`.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

RUN_ID_PATTERN = r"^\d{8}-\d{6}-[0-9a-f]{4}$"
UNIT_ID_PATTERN = r"^U-\d{4,}$"

_RUN_ID_RE = re.compile(RUN_ID_PATTERN)
_UNIT_ID_RE = re.compile(UNIT_ID_PATTERN)

BRANCH_PREFIX = "codecoremorph/"


def new_run_id(now: datetime | None = None, suffix: str | None = None) -> str:
    """Return a new run ID.

    UTC keeps IDs monotonic across DST changes and identical across machines. ``now`` and
    ``suffix`` exist for tests; a naive ``now`` is rejected so local time can't leak in.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        raise ValueError("new_run_id() needs a timezone-aware datetime")
    if suffix is None:
        suffix = secrets.token_hex(2)
    run_id = f"{now.astimezone(UTC):%Y%m%d-%H%M%S}-{suffix}"
    return validate_run_id(run_id)


def is_run_id(value: str) -> bool:
    return _RUN_ID_RE.fullmatch(value) is not None


def validate_run_id(value: str) -> str:
    """Return ``value`` unchanged if it is a well-formed run ID, else raise ``ValueError``."""
    if not is_run_id(value):
        raise ValueError(f"invalid run ID {value!r}: expected YYYYMMDD-HHMMSS-xxxx")
    return value


def run_branch(run_id: str) -> str:
    """Return the migration branch name of a run (SPEC §7.9)."""
    return BRANCH_PREFIX + validate_run_id(run_id)


def unit_id(seq: int) -> str:
    """Return the work-unit ID for a 1-based sequence number, e.g. ``U-0042``."""
    if seq < 1:
        raise ValueError(f"unit sequence numbers start at 1, got {seq}")
    return f"U-{seq:04d}"


def is_unit_id(value: str) -> bool:
    return _UNIT_ID_RE.fullmatch(value) is not None


def validate_unit_id(value: str) -> str:
    if not is_unit_id(value):
        raise ValueError(f"invalid unit ID {value!r}: expected U-NNNN")
    return value
