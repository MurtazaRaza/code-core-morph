"""The C++ runtime of a Java → C++ port (SPEC §11.12, D-92), copied into the project by the C++
adapter's scaffold:

- ``codecoremorph_exceptions.hpp``: C++ exceptions that name the Java type they stand for;
- ``codecoremorph_java.hpp``: Java's semantics where C++'s differ (wrapping arithmetic, ``/ by
  zero``, masked shifts, ``String.hashCode`` and ``length`` over UTF-16 units);
- ``codecoremorph_bridge.hpp`` and ``.cpp``: the bridge's C++ helpers and its error record, over
  the C ABI of ``codecoremorph.adapters.java.port_runtime`` (``codecoremorph_bridge.h``);
- ``codecoremorph_jni.h`` and ``.c``: the fixed part of the JNI glue (``JNI_OnLoad``, the
  thread's ``JNIEnv``, UTF-8 byte arrays, global references), beside the generated
  ``codecoremorph_jni.g.c`` (:mod:`codecoremorph.adapters.cpp.jniglue`).

The first two stay after the retirement; the bridge files go with it.
"""
