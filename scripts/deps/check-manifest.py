"""Check scripts/deps/manifest.txt: row format, pinned hashes, platform coverage (SPEC §7.15).

Usage: python scripts/deps/check-manifest.py [MANIFEST]
Exits 0 when the manifest is well-formed, 1 otherwise. The pre-commit hook runs it on every change.
"""

from __future__ import annotations

import sys
from pathlib import Path

from manifest import check, parse, parse_coverage

DEFAULT_MANIFEST = Path(__file__).resolve().with_name("manifest.txt")


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else DEFAULT_MANIFEST
    raw = path.read_bytes()
    errors: list[str] = []
    if b"\r" in raw:
        errors.append("the manifest must use LF line endings (a CR breaks `read` in sh)")
    text = raw.decode("utf-8")
    rows, row_errors = parse(text)
    coverage, coverage_errors = parse_coverage(text)
    errors += row_errors + coverage_errors + check(rows, coverage)
    if not rows:
        errors.append("the manifest has no rows")
    for error in errors:
        print(f"{path}: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"{path}: {len(rows)} rows OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
