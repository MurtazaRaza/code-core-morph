"""Re-download manifest URLs and rewrite their SHA-256 hashes (maintainer tool, SPEC §7.15).

Usage:
  python scripts/deps/refresh-hashes.py [--group G] [--tool T] [--set-version V] [--dry-run]

--set-version needs --tool. It replaces the old version in the version, url and bin_dir
fields of that tool's rows before downloading, which is how version bumps are done.
Comments, blank lines and row order are kept. Only HTTPS URLs are downloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from manifest import Row, check, parse, parse_coverage

DEFAULT_MANIFEST = Path(__file__).resolve().with_name("manifest.txt")
_ATTEMPTS = 4  # the first try plus 3 retries, like the install scripts
_CHUNK = 1 << 20


def fetch_sha256(url: str) -> str:
    if not url.startswith("https://"):
        raise ValueError(f"refusing to download a non-HTTPS URL: {url}")
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            digest = hashlib.sha256()
            request = urllib.request.Request(url, headers={"User-Agent": "codecoremorph-deps"})
            with urllib.request.urlopen(request, timeout=60) as response:
                while chunk := response.read(_CHUNK):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError as exc:
            if attempt == _ATTEMPTS:
                raise
            print(f"  attempt {attempt} failed ({exc}); retrying", file=sys.stderr)
            time.sleep(2)
    raise AssertionError("unreachable")


def refresh(
    text: str,
    *,
    group: str | None = None,
    tool: str | None = None,
    new_version: str | None = None,
    fetch: Callable[[str], str] = fetch_sha256,
) -> tuple[str, list[str]]:
    """Return the updated manifest text and a list of human-readable changes."""
    rows, errors = parse(text)
    if errors:
        raise ValueError("fix the manifest first:\n" + "\n".join(errors))
    changes: list[str] = []
    updated: dict[int, Row] = {}
    for row in rows:
        if (group and row.group != group) or (tool and row.tool != tool):
            continue
        new = row
        if new_version and new_version != row.version:
            new = row.with_values(
                version=new_version,
                url=row.url.replace(row.version, new_version),
                bin_dir=row.bin_dir.replace(row.version, new_version),
            )
        print(f"downloading {new.url}", file=sys.stderr)
        new = new.with_values(sha256=fetch(new.url))
        name = f"{row.group}|{row.tool}|{row.platform}"
        if new.version != row.version:
            changes.append(f"{name}: {row.version} -> {new.version}")
        elif new.sha256 != row.sha256:
            changes.append(f"{name}: sha256 updated for {row.version}")
        updated[row.line] = new
    if not updated:
        raise ValueError("no manifest rows match the given --group/--tool")

    lines = text.split("\n")
    for number, row in updated.items():
        lines[number - 1] = row.render()
    new_text = "\n".join(lines)
    new_rows, _ = parse(new_text)
    coverage, _ = parse_coverage(new_text)
    problems = check(new_rows, coverage)
    if problems:
        raise ValueError("the result would be inconsistent:\n" + "\n".join(problems))
    return new_text, changes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Re-download manifest URLs and rewrite hashes.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--group")
    parser.add_argument("--tool")
    parser.add_argument("--set-version", dest="new_version")
    parser.add_argument("--dry-run", action="store_true", help="show changes, don't write")
    args = parser.parse_args(argv)
    if args.new_version and not args.tool:
        parser.error("--set-version needs --tool")

    text = args.manifest.read_bytes().decode("utf-8")
    try:
        new_text, changes = refresh(
            text, group=args.group, tool=args.tool, new_version=args.new_version
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for change in changes:
        print(change)
    if not changes:
        print("all hashes are current")
    if args.dry_run or new_text == text:
        return 0
    tmp = args.manifest.with_suffix(".tmp")
    tmp.write_bytes(new_text.encode("utf-8"))
    tmp.replace(args.manifest)
    print(f"updated {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
