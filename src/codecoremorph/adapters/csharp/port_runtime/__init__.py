"""The C# side's runtime of a port from C# (SPEC §11.9, §11.10, D-78, D-84), the same for every
target:

- ``codecoremorph_bridge.h``: the bridge's C ABI (statuses, decimals, handles, the error
  record), which a C++ bridge implements in ``codecoremorph_bridge.cpp`` and a Rust bridge in
  its ``ccm`` module;
- ``CodeCoreMorph.Interop.cs``: the C# support of the façades.

Both go with the bridge at the retirement, unless ``keep_source_api`` keeps the façades.
"""
