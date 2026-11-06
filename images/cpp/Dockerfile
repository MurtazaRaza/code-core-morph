# CodeCoreMorph C++ toolchain image (SPEC §11.1, D-23).
#
# System packages come from Ubuntu; the compiler, CMake, Ninja and GoogleTest come from
# scripts/install-toolchain.sh and the pinned manifest, so Docker and native mode use the same
# versions. Build it from the repository root with scripts/build-image.sh cpp (.bat on Windows).
#
# Ubuntu 22.04: the LLVM 18 Linux release is built on Ubuntu 18.04 and needs libtinfo5, which
# 24.04 no longer ships.
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

# autoconf, automake and libtool regenerate an autotools project's configure script when it
# doesn't ship one (a C project built with Make, D-68).
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential ca-certificates ccache curl libtinfo5 libtool \
        libxml2 python3 python3-venv unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# The toolchain, installed by the same script as native installs. Downloads are verified
# against the manifest; the archives are dropped afterwards to keep the image small.
COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh cpp --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads

# The libclang binding for the index helper, pinned by hash to the wheel uv.lock locks.
COPY images/cpp/requirements.txt /opt/codecoremorph/requirements.txt
RUN python3 -m venv /opt/codecoremorph/venv \
    && /opt/codecoremorph/venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/codecoremorph/requirements.txt

# Commands get the toolchain through toolchains/cpp.env (applied by the Docker executor), and
# the helper's interpreter as python3.
ENV PATH=/opt/codecoremorph/venv/bin:$PATH
LABEL org.codecoremorph.language="cpp" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/cpp.env"
USER 1000:1000
WORKDIR /workspace
