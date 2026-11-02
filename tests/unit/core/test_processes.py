from __future__ import annotations

import os
import subprocess
import sys

from codecoremorph.core.processes import pid_is_running


def test_pid_is_running() -> None:
    assert pid_is_running(os.getpid())
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert not pid_is_running(process.pid)
    assert not pid_is_running(0)
    assert not pid_is_running(-5)
