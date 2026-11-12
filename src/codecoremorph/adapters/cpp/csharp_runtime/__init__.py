"""The C++ runtime of a C# → C++ port (SPEC §11.9, D-78), copied into the project by the C++
adapter's scaffold:

- ``codecoremorph_decimal.hpp``: .NET's ``decimal`` for C++ (``ccm::Decimal``), bit for bit;
- ``codecoremorph_exceptions.hpp``: C++ exceptions that name the .NET type they stand for;
- ``codecoremorph_bridge.hpp`` and ``.cpp``: the bridge's C++ helpers and its error record, over
  the C ABI of ``codecoremorph.adapters.csharp.port_runtime`` (``codecoremorph_bridge.h``),
  which ships there with the C# support of the façades, since every target shares them (D-84).

The first two stay after the retirement; the bridge files go with it.
"""
