# The M11 benchmark's toolchain (bench/csharp-port-pin.json, D-82): the image for ports from C#
# with libsodium's development files, the header and library the C++ the port writes includes
# and links. Ubuntu 22.04's libsodium-dev is 1.0.18, the version libsodium-core 1.3.1 loads from
# its NuGet package (libsodium 1.0.18.2). bench/plan.py and bench/run.py build it with the
# toolchain image as BASE and run with its ID as sandbox.image.
ARG BASE
FROM ${BASE}
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsodium-dev \
    && rm -rf /var/lib/apt/lists/*
USER 1000:1000
