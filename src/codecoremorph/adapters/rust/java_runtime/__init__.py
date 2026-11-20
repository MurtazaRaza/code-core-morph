"""The Rust runtime of a Java → Rust port (SPEC §11.12, D-92), copied into the workspace by the
Rust adapter's scaffold:

- ``codecoremorph_java``: a workspace member, Java's exceptions (``Exception``, by Java type
  name) and semantics (``lang``: wrapping arithmetic, ``/ by zero``, masked shifts,
  ``String.hashCode`` and ``length`` over UTF-16 units); it stays after the retirement;
- ``ccm.rs``: the bridge's runtime (statuses, the error record, strings, arrays, handles, calls
  into Java), each crate's ``src/bridge/ccm.rs``;
- ``jni.rs``: the fixed part of the JNI glue (``JNI_OnLoad``, the thread's ``JNIEnv``, UTF-8
  byte arrays, numbers, global references, the trampolines' method calls), over bindgen's
  ``jni_sys.rs``, beside the generated ``jni_g.rs`` (:mod:`codecoremorph.adapters.rust.jniglue`).
  The bridge ``include!``s the three, so cbindgen leaves the JNI functions out of its header.

The bridge files go with the bridge at the retirement, unless ``keep_source_api`` keeps the
façades.
"""
