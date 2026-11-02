"""The built-in C++ adapter (SPEC §11.1), registered through the codecoremorph.adapters entry
point."""

from codecoremorph.adapters.cpp.adapter import CppAdapter

__all__ = ["CppAdapter"]
