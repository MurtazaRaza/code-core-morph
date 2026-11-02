"""The scrubbed environment native commands run with (SPEC §7.8, §7.15).

Only allowlisted host variables pass through, so credentials in the user's environment
(``ANTHROPIC_API_KEY`` and the like) never reach LLM-written code. The toolchain file
``<CODECOREMORPH_HOME>/toolchains/<lang>.env`` written by ``install-toolchain`` is applied on
top: ``KEY=VALUE`` lines, where ``PATH_PREPEND`` adds directories to the front of PATH.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

from codecoremorph.core.secrets import is_secret_env_name

_COMMON = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ")
_POSIX = ("HOME", "USER", "LOGNAME", "TMPDIR", "SHELL")
_WINDOWS = (
    "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "USERPROFILE", "USERNAME", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "OS",
)  # fmt: skip
DEFAULT_ALLOWLIST = frozenset(_COMMON + (_WINDOWS if sys.platform == "win32" else _POSIX))
PATH_PREPEND = "PATH_PREPEND"


def _key(name: str) -> str:
    return name.upper() if sys.platform == "win32" else name


def scrubbed_environment(
    host: Mapping[str, str] | None = None, extra_allowlist: Iterable[str] = ()
) -> dict[str, str]:
    """Allowlisted host variables. Credential-like names never pass, even if allowlisted."""
    allowed = {_key(n) for n in DEFAULT_ALLOWLIST} | {_key(n) for n in extra_allowlist}
    return {
        key: value
        for key, value in (os.environ if host is None else host).items()
        if _key(key) in allowed and not is_secret_env_name(key)
    }


def merge_environment(base: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    """``base`` updated with ``extra``; names compare case-insensitively on Windows."""
    merged = dict(base)
    for key, value in extra.items():
        for existing in [k for k in merged if _key(k) == _key(key)]:
            del merged[existing]
        merged[key] = value
    return merged


def parse_env_file(text: str) -> dict[str, str]:
    """``KEY=VALUE`` lines; blank lines and ``#`` comments are skipped, quotes are stripped."""
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"line {number}: expected KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def apply_toolchain_env(env: Mapping[str, str], toolchain: Mapping[str, str]) -> dict[str, str]:
    result = dict(env)
    for key, value in toolchain.items():
        if key == PATH_PREPEND:
            path_key = next((k for k in result if _key(k) == _key("PATH")), "PATH")
            current = result.get(path_key, "")
            result[path_key] = value + (os.pathsep + current if current else "")
        else:
            result = merge_environment(result, {key: value})
    return result


def load_toolchain_env(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    return parse_env_file(path.read_text(encoding="utf-8"))
