from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from codecoremorph.core.paths import codecoremorph_home

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "cpp11-to-cpp20.yaml"
# The developer's CODECOREMORPH_HOME, read before any test isolates it: `toolchain` tests use
# the native toolchain installed there.
REAL_HOME = codecoremorph_home()

GitRunner = Callable[..., str]

_TEST_GITCONFIG = """\
[user]
\tname = Test User
\temail = test@example.com
[init]
\tdefaultBranch = main
[core]
\tautocrlf = false
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from the real CODECOREMORPH_HOME."""
    home = tmp_path / "ccm-home"
    monkeypatch.setenv("CODECOREMORPH_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def test_gitconfig(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("git") / "gitconfig"
    path.write_text(_TEST_GITCONFIG, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def isolated_git(test_gitconfig: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git in tests ignores the developer's global and system config (autocrlf, hooks,
    signing) and sees a fixed identity."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(test_gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, input=stdin, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.decode()}")
    return result.stdout.decode("utf-8", "replace")


@pytest.fixture
def git() -> GitRunner:
    """Run git in a test repository: ``git(path, "status")``. Never this repository."""
    return _git


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A temporary repository with two commits on main."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "vector.h").write_bytes(b"#pragma once\ntypedef int Index;\n")
    (root / "src" / "vector.cpp").write_bytes(
        b'#include "vector.h"\r\nint size() { return 0; }\r\n'
    )
    (root / "README.md").write_bytes(b"# demo\n")
    (root / ".gitignore").write_bytes(b"build/\n*.o\n")
    _git(root.parent, "init", "--quiet", str(root))
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "initial")
    (root / "src" / "util.h").write_bytes(b"inline int one() { return 1; }\n")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "-m", "add util")
    return root


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Undo handler and level changes made by configure_logging()."""
    root = logging.getLogger()
    package = logging.getLogger("codecoremorph")
    handlers, root_level, package_level = list(root.handlers), root.level, package.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(root_level)
    package.setLevel(package_level)


@pytest.fixture(scope="session")
def cpp_toolchain_env() -> Path:
    """The installed native C++ toolchain's environment file; skips when there is none."""
    env = REAL_HOME / "toolchains" / "cpp.env"
    if not env.is_file():
        pytest.skip(f"no native C++ toolchain at {env}; run: codecoremorph toolchain install cpp")
    return env


@pytest.fixture
def rust_toolchain_env() -> Path:
    """The installed native Rust toolchain's environment file (it holds the C++ toolchain
    too, D-56); skips when there is none."""
    env = REAL_HOME / "toolchains" / "rust.env"
    if not env.is_file():
        pytest.skip(f"no native Rust toolchain at {env}; run: codecoremorph toolchain install rust")
    return env


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--record-cassettes",
        action="store_true",
        default=False,
        help="Record LLM cassettes (tests/cassettes/) instead of replaying them.",
    )


@pytest.fixture
def record_cassettes(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--record-cassettes"))
