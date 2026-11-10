"""The runtime of a C# → C++ port (SPEC §11.9, D-78), copied into the project by the C++
adapter's scaffold:

- ``codecoremorph_decimal.hpp``: .NET's ``decimal`` for C++ (``ccm::Decimal``), bit for bit;
- ``codecoremorph_exceptions.hpp``: C++ exceptions that name the .NET type they stand for;
- ``codecoremorph_bridge.h``, ``.hpp`` and ``.cpp``: the bridge's C types, its C++ helpers and
  its error record;
- ``CodeCoreMorph.Interop.cs``: the C# support of the façades.

The first two stay after the retirement; the bridge files and the C# go with it.
"""
