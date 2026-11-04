"""Download the pinned archives for an offline install on another machine (FR-1.12, §7.15).

Usage:
  python scripts/deps/fetch-offline.py DIR [--platform P] [--group G]... [--manifest FILE]

Downloads every manifest row of the groups (default: cpp) for the platform (default: this
machine's) into DIR, checks each against its pinned SHA-256 and saves it under its cache name,
SHA256.EXT, the layout of a CODECOREMORPH_HOME's downloads directory. Files already in DIR
with the right hash are kept. Then copy DIR to the machine without internet access and run
`install-toolchain --offline DIR` (or `codecoremorph toolchain install cpp --offline DIR`)
there, with the same CodeCoreMorph version, so both read the same manifest.

Exit codes: 0 success, 1 download or checksum failure, 2 usage error. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

from manifest import ANY_PLATFORM, PLATFORMS, Row, parse

DEFAULT_MANIFEST = Path(__file__).resolve().with_name("manifest.txt")
EXIT_DOWNLOAD = 1
EXIT_USAGE = 2
_ATTEMPTS = 4  # the first try plus 3 retries, like the install scripts
_CHUNK = 1 << 20


def this_platform() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    return f"{name}-{arch}"


def cache_name(row: Row) -> str:
    """The file name the install scripts give a verified archive in downloads/."""
    extension = "bin" if row.archive == "file" else row.archive
    return f"{row.sha256}.{extension}"


def download(url: str, dest: Path) -> None:
    if not url.startswith(("https://", "file://")):
        raise ValueError(f"refusing to download from a non-HTTPS URL: {url}")
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "codecoremorph-deps"})
            with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
            return
        except OSError as exc:
            dest.unlink(missing_ok=True)
            if attempt == _ATTEMPTS:
                raise
            print(f"  download failed (attempt {attempt} of {_ATTEMPTS}; {exc}); retrying")
            time.sleep(2)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def rows_for(rows: Sequence[Row], target: str, groups: Sequence[str]) -> list[Row]:
    return [r for r in rows if r.group in groups and r.platform in (target, ANY_PLATFORM)]


def fetch(
    rows: Sequence[Row], directory: Path, fetcher: Callable[[str, Path], None] | None = None
) -> list[str]:
    """Download ``rows`` into ``directory``; returns the problems (empty on success)."""
    fetcher = fetcher or download
    directory.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for row in rows:
        dest = directory / cache_name(row)
        label = f"{row.tool} {row.version} ({row.platform})"
        if dest.is_file() and sha256_of(dest) == row.sha256:
            print(f"codecoremorph: {label} is already in {directory}")
            continue
        part = dest.with_name(dest.name + ".part")
        print(f"codecoremorph: downloading {label} from {row.url}")
        try:
            fetcher(row.url, part)
        except (OSError, ValueError) as exc:
            part.unlink(missing_ok=True)
            problems.append(f"download failed: {row.url} ({exc})")
            continue
        actual = sha256_of(part)
        if actual != row.sha256:
            part.unlink()
            problems.append(
                f"checksum mismatch for {row.url}: expected {row.sha256}, got {actual}. "
                "The file was deleted."
            )
            continue
        part.replace(dest)
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fetch-offline",
        description="Download the pinned archives for an offline install on another machine.",
    )
    parser.add_argument("directory", type=Path, help="where to put the archives")
    parser.add_argument(
        "--platform",
        default=this_platform(),
        choices=PLATFORMS,
        help="the platform of the machine without internet access (default: this one)",
    )
    parser.add_argument(
        "--group",
        action="append",
        dest="groups",
        help="manifest group to fetch, e.g. cpp or dev (repeatable; default: cpp)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(os.environ.get("CODECOREMORPH_MANIFEST") or DEFAULT_MANIFEST),
        help="the manifest to read (default: CODECOREMORPH_MANIFEST, else the bundled one)",
    )
    args = parser.parse_args(argv)
    groups: list[str] = args.groups or ["cpp"]

    try:
        text = args.manifest.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"codecoremorph: error: can't read the manifest {args.manifest}: {exc}")
        return EXIT_USAGE
    rows, errors = parse(text)
    if errors:
        print("codecoremorph: error: the manifest is malformed:\n  " + "\n  ".join(errors))
        return EXIT_USAGE
    wanted = rows_for(rows, args.platform, groups)
    if not wanted:
        print(
            f"codecoremorph: error: the manifest pins nothing in {', '.join(groups)} "
            f"for {args.platform}"
        )
        return EXIT_USAGE
    problems = fetch(wanted, args.directory)
    for problem in problems:
        print(f"codecoremorph: error: {problem}")
    if problems:
        return EXIT_DOWNLOAD
    commands = [
        "bootstrap-dev --offline DIR" if g == "dev" else f"install-toolchain --offline DIR {g}"
        for g in groups
    ]
    print(
        f"codecoremorph: {len(wanted)} archives for {args.platform} are in {args.directory}. "
        f"Copy the directory to the other machine and run there: {'; '.join(commands)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
