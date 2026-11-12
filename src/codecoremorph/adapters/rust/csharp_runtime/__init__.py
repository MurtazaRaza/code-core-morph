"""The Rust runtime of a C# → Rust port (SPEC §11.10, D-84), copied into the project by the Rust
adapter's scaffold:

- ``codecoremorph_runtime/``: a workspace member, safe Rust, kept after the retirement:
  ``Decimal``, .NET's ``decimal`` bit for bit, and ``Exception``, .NET's exceptions by type
  name, message, parameter and fields;
- ``ccm.rs``: each crate's ``src/bridge/ccm.rs``, the bridge's runtime over the C ABI of
  ``codecoremorph.adapters.csharp.port_runtime`` (``codecoremorph_bridge.h``): ``guard``, the
  error record, strings, arrays, decimals, handles, calls into C#. It goes with the bridge.
"""
