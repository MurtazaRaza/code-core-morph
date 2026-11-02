"""PatchEngine: applies an edit batch to the worktree, all or nothing (SPEC §7.4, FR-3.3).

1. **Plan in memory.** Every edit is checked and applied to in-memory copies of the files,
   in batch order (a later edit sees the result of earlier ones). If any edit is rejected,
   nothing is written, and every rejection is reported.
2. **Stage.** Each changed file is written to a temporary file next to it.
3. **Commit.** Temporary files are renamed over their targets, then deletions happen. If
   any step fails, the files already replaced are restored from memory.

If restoring fails too, :class:`PatchError` is raised; the attempt's git rollback (FR-6.3)
is the final safety net.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from codecoremorph.core.errors import CodeCoreMorphError, ExitCode
from codecoremorph.core.globs import PathMatcher
from codecoremorph.core.models.edits import (
    ApplyResult,
    CreateEdit,
    Edit,
    EditBatch,
    EditRejection,
    RejectionReason,
    ReplaceEdit,
)
from codecoremorph.patching.matcher import MatchFailure, find_match
from codecoremorph.patching.paths import PathProblem, SafePath, check_edit_path
from codecoremorph.patching.textfile import BinaryFileError, NormalizedView, TextFile

_NEW_FILE = TextFile("utf-8", b"", "")
_REPLACE_ATTEMPTS = 5  # Windows refuses a rename while a scanner or editor has the file open


class PatchError(CodeCoreMorphError):
    """Writing failed and the worktree could not be restored. Roll the attempt back."""

    exit_code = ExitCode.INTERNAL


@dataclass
class _FileState:
    rel: str
    target: Path
    first_edit: int
    original: bytes | None  # None: the file didn't exist
    exists: bool
    binary: bool
    encoding: TextFile = _NEW_FILE
    raw: str | None = None  # current text (None when absent or binary)
    edits: list[int] = field(default_factory=list[int])


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


# The filesystem primitives are module functions so tests can inject failures.
def _write_file(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_file(source: Path, target: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            source.replace(target)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def _delete_file(path: Path) -> None:
    path.unlink()


class PatchEngine:
    """Applies :class:`EditBatch` edits under ``root`` (the run worktree)."""

    def __init__(self, root: Path, *, exclude: Iterable[str] = ()):
        self.root = root.resolve()
        self._exclude = PathMatcher(exclude)

    def preview(
        self, edits: EditBatch | Sequence[Edit]
    ) -> tuple[ApplyResult, dict[str, bytes | None]]:
        """Plan the batch without writing. Returns the result and each changed file's new
        bytes (None for deletions)."""
        result, states = self._plan(edits)
        if not result.applied:
            return result, {}
        return result, {s.rel: self._new_bytes(s) for s in states}

    def apply(self, edits: EditBatch | Sequence[Edit]) -> ApplyResult:
        result, states = self._plan(edits)
        if not result.applied:
            return result
        failure = self._write(states)
        if failure is not None:
            return ApplyResult(applied=False, rejections=[failure])
        return result

    # ------------------------------------------------------------------ planning

    def _plan(self, edits: EditBatch | Sequence[Edit]) -> tuple[ApplyResult, list[_FileState]]:
        items = edits.edits if isinstance(edits, EditBatch) else list(edits)
        states: dict[str, _FileState] = {}
        rejections: list[EditRejection] = []
        tolerant: list[int] = []

        for index, edit in enumerate(items):

            def reject(
                reason: RejectionReason,
                message: str,
                failure: MatchFailure | None = None,
                *,
                _index: int = index,
                _edit: Edit = edit,
            ) -> None:
                rejections.append(
                    EditRejection(
                        edit_index=_index,
                        op=_edit.op,
                        path=_edit.path,
                        reason=reason,
                        message=message,
                        match_lines=failure.lines if failure else [],
                        candidate=failure.candidate if failure else None,
                    )
                )

            checked = check_edit_path(self.root, edit.path, self._exclude)
            if isinstance(checked, PathProblem):
                reject(checked.reason, checked.message)
                continue
            key = os.path.normcase(str(checked.target.resolve()))
            state = states.get(key)
            if state is None:
                loaded = self._load(checked, index)
                if isinstance(loaded, PathProblem):
                    reject(loaded.reason, loaded.message)
                    continue
                state = loaded

            if isinstance(edit, ReplaceEdit):
                if not state.exists:
                    reject(RejectionReason.FILE_MISSING, "the file doesn't exist")
                    continue
                if state.binary or state.raw is None:
                    reject(RejectionReason.BINARY_FILE, "the file is binary and can't be edited")
                    continue
                view = NormalizedView(state.raw)
                found = find_match(view.text, _normalize(edit.search), _normalize(edit.replace))
                if isinstance(found, MatchFailure):
                    reject(found.reason, found.message, found)
                    continue
                if not state.encoding.can_encode(found.replacement):
                    reject(
                        RejectionReason.ENCODING,
                        f"the replacement has characters the file's encoding "
                        f"({state.encoding.encoding}) can't represent",
                    )
                    continue
                state.raw = view.splice(found.start, found.end, found.replacement)
                if found.tolerant:
                    tolerant.append(index)
            elif isinstance(edit, CreateEdit):
                if state.exists:
                    reject(
                        RejectionReason.FILE_EXISTS,
                        "the file already exists; change it with replace edits",
                    )
                    continue
                content = _normalize(edit.content)
                if not state.encoding.can_encode(content):
                    reject(RejectionReason.ENCODING, "the content can't be encoded as UTF-8")
                    continue
                state.raw, state.exists, state.binary = content, True, False
            else:  # DeleteEdit
                if not state.exists:
                    reject(RejectionReason.FILE_MISSING, "the file doesn't exist")
                    continue
                state.raw, state.exists, state.binary = None, False, False
            state.edits.append(index)
            states[key] = state

        if rejections:
            return ApplyResult(
                applied=False, rejections=rejections, whitespace_tolerant=tolerant
            ), []
        changed = [s for s in states.values() if self._new_bytes(s) != s.original]
        return (
            ApplyResult(
                applied=True,
                changed_files=sorted(s.rel for s in changed),
                whitespace_tolerant=tolerant,
            ),
            changed,
        )

    def _load(self, path: SafePath, index: int) -> _FileState | PathProblem:
        target = path.target
        if target.is_dir():
            return PathProblem(RejectionReason.UNSAFE_PATH, "the path is a directory")
        if not target.exists():
            return _FileState(path.rel, target, index, None, exists=False, binary=False)
        try:
            data = target.read_bytes()
        except OSError as exc:
            return PathProblem(RejectionReason.WRITE_FAILED, f"the file can't be read: {exc}")
        try:
            decoded = TextFile.decode(data)
        except BinaryFileError:
            return _FileState(path.rel, target, index, data, exists=True, binary=True)
        except UnicodeDecodeError:  # a BOM followed by invalid data
            return _FileState(path.rel, target, index, data, exists=True, binary=True)
        return _FileState(
            path.rel,
            target,
            index,
            data,
            exists=True,
            binary=False,
            encoding=decoded,
            raw=decoded.text,
        )

    @staticmethod
    def _new_bytes(state: _FileState) -> bytes | None:
        if not state.exists:
            return None
        if state.binary or state.raw is None:
            return state.original
        return state.encoding.encode(state.raw)

    # ------------------------------------------------------------------ writing

    def _write(self, states: list[_FileState]) -> EditRejection | None:
        writes = [(s, data) for s in states if (data := self._new_bytes(s)) is not None]
        deletes = [s for s in states if not s.exists]
        staged: list[tuple[Path, _FileState]] = []
        created_dirs: list[Path] = []
        done: list[_FileState] = []
        current: _FileState | None = None
        try:
            for state, data in writes:
                current = state
                created_dirs += _make_parents(state.target.parent)
                tmp = state.target.with_name(f".{state.target.name}.ccm-{secrets.token_hex(4)}.tmp")
                _write_file(tmp, data)
                staged.append((tmp, state))
                if state.original is not None:
                    shutil.copymode(state.target, tmp)
            for tmp, state in staged:
                current = state
                _replace_file(tmp, state.target)
                done.append(state)
            for state in deletes:
                current = state
                _delete_file(state.target)
                done.append(state)
        except OSError as exc:
            self._undo(done, staged, created_dirs)
            failed = current or states[0]
            return EditRejection(
                edit_index=failed.first_edit,
                op="delete" if not failed.exists else ("replace" if failed.original else "create"),
                path=failed.rel,
                reason=RejectionReason.WRITE_FAILED,
                message=f"writing {failed.rel} failed ({exc}); no file was changed",
            )
        return None

    @staticmethod
    def _undo(
        done: list[_FileState], staged: list[tuple[Path, _FileState]], dirs: list[Path]
    ) -> None:
        problems: list[str] = []
        for state in reversed(done):
            try:
                if state.original is None:
                    state.target.unlink(missing_ok=True)
                else:
                    tmp = state.target.with_name(f".{state.target.name}.ccm-restore.tmp")
                    tmp.unlink(missing_ok=True)
                    tmp.write_bytes(state.original)
                    tmp.replace(state.target)
            except OSError as exc:
                problems.append(f"{state.rel}: {exc}")
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        for directory in reversed(dirs):
            # Not empty, or already gone: leave it for the git rollback.
            with contextlib.suppress(OSError):
                directory.rmdir()
        if problems:
            raise PatchError(
                "an edit batch failed half-way and some files could not be restored",
                reason="; ".join(problems),
                hint="Roll the attempt back with git (reset --hard plus clean -fd).",
            )


def _make_parents(directory: Path) -> list[Path]:
    """Create ``directory`` and missing parents; return the ones created, outermost first."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
    return list(reversed(missing))
