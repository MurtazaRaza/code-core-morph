"""CodeCoreMorph: codebase migration with LLM agents in a compile, test and fix loop."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("codecoremorph")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
