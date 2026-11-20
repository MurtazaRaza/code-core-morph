"""The Java side's runtime of a port from Java (SPEC §11.12, D-92), the same for every target:

- ``codecoremorph_bridge.h``: the bridge's C ABI with Java's names (statuses, handles, the error
  record), which a C++ bridge implements in ``codecoremorph_bridge.cpp`` and a Rust bridge in its
  ``ccm`` module;
- ``CcmInterop.java``: the façades' support (statuses into exceptions, the cleaner of handles,
  identities, UTF-8), in ``codecoremorph.interop`` beside the generated ``CcmNative.java``;
- ``CcmExceptions.java``: the factory of the project's own exceptions, which the scaffold writes
  empty and each unit fills.

They go with the bridge at the retirement, unless ``keep_source_api`` keeps the façades.
"""
