"""The C# language adapter (SPEC §11.3, D-65)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codecoremorph.adapters.csharp.adapter import CSharpAdapter

__all__ = ["CSharpAdapter"]


def __getattr__(name: str) -> Any:
    # Loaded on first use: the C# side of a port (`ports`), which the target languages'
    # adapters import, must not load the C# adapter, which imports them (D-84).
    if name == "CSharpAdapter":
        from codecoremorph.adapters.csharp.adapter import CSharpAdapter

        return CSharpAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
