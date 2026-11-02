"""Command execution in the Docker sandbox or natively on the host (SPEC §7.8)."""

from typing import TYPE_CHECKING

from codecoremorph.sandbox.executor import DEFAULT_OUTPUT_CAP, Executor
from codecoremorph.sandbox.native import NativeExecutor
from codecoremorph.sandbox.selection import ExecutorChoice, choose_executor

if TYPE_CHECKING:  # both executors must satisfy the protocol; pyright checks this
    from codecoremorph.sandbox.docker.executor import DockerExecutor

    def _conforms(native: NativeExecutor, docker: DockerExecutor) -> tuple[Executor, Executor]:
        return native, docker


__all__ = ["DEFAULT_OUTPUT_CAP", "Executor", "ExecutorChoice", "NativeExecutor", "choose_executor"]
