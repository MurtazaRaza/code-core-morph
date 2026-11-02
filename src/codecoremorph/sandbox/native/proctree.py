"""Starting a command so that its whole process tree can be killed (POSIX and Windows).

- POSIX: the command leads a new session, so its process group holds every descendant that
  doesn't deliberately escape. A timeout sends SIGTERM to the group, then SIGKILL.
- Windows: the command is placed in a Job Object right after it starts; descendants join
  the job automatically. A timeout terminates the job, then ``taskkill /T`` sweeps up any
  process that started before the assignment. Closing the job (``kill-on-close``) also ends
  stray background processes once the command is done.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

_TERM_GRACE_S = 2.0


class ManagedProcess:
    def __init__(self, popen: subprocess.Popen[bytes], job: _Job | None):
        self.popen = popen
        self._job = job

    def kill_tree(self) -> None:
        if sys.platform == "win32":
            if self._job is not None:
                self._job.terminate()
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.popen.pid)],
                capture_output=True,
                check=False,
            )
        else:
            _kill_group(self.popen)

    def close(self) -> None:
        if self._job is not None:
            self._job.close()
            self._job = None


def spawn(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdout: IO[bytes],
    stderr: IO[bytes],
    stdin_pipe: bool,
) -> ManagedProcess:
    stdin = subprocess.PIPE if stdin_pipe else subprocess.DEVNULL
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        popen = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        job: _Job | None = None
        try:
            job = _Job()
            job.assign(popen.pid)
        except OSError as exc:
            log.debug("couldn't put pid %d in a job object (%s); using taskkill", popen.pid, exc)
            if job is not None:
                job.close()
            job = None
        return ManagedProcess(popen, job)
    popen = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        close_fds=True,
    )
    return ManagedProcess(popen, None)


if sys.platform != "win32":
    import signal

    def _kill_group(popen: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(popen.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + _TERM_GRACE_S
        while popen.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError):  # descendants that ignored SIGTERM
            os.killpg(popen.pid, signal.SIGKILL)

    class _Job:  # never created on POSIX
        def terminate(self) -> None: ...
        def close(self) -> None: ...

else:
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _check(ok: object) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    class _Job:
        def __init__(self) -> None:
            handle = _k32.CreateJobObjectW(None, None)
            _check(handle)
            self._handle: int | None = handle
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _check(
                _k32.SetInformationJobObject(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                )
            )

        def assign(self, pid: int) -> None:
            process = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            _check(process)
            try:
                _check(_k32.AssignProcessToJobObject(self._handle, process))
            finally:
                _k32.CloseHandle(process)

        def terminate(self) -> None:
            if self._handle is not None:
                _k32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            if self._handle is not None:
                _k32.CloseHandle(self._handle)
                self._handle = None

    def _kill_group(popen: subprocess.Popen[bytes]) -> None:  # POSIX only
        raise NotImplementedError
