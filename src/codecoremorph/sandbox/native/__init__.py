"""Native executor: commands on the host, with a scrubbed environment and tree kills."""

from codecoremorph.sandbox.native.executor import REDUCED_ISOLATION_WARNING, NativeExecutor

__all__ = ["REDUCED_ISOLATION_WARNING", "NativeExecutor"]
