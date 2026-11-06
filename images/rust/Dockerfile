# CodeCoreMorph Rust toolchain image (SPEC §11.5, D-56).
#
# A port from C++ builds the project with CMake and the crates with Cargo (through Corrosion),
# so this image holds the C++ toolchain of images/cpp too: scripts/install-toolchain.sh rust
# installs both from the pinned manifest, as native installs do. Build it from the repository
# root with scripts/build-image.sh rust (.bat on Windows).
#
# Ubuntu 22.04, as images/cpp: the LLVM 18 Linux release needs libtinfo5. A port from C (D-68)
# builds with Make, and autoconf, automake and libtool regenerate a configure script.
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential ca-certificates ccache curl libtinfo5 libtool \
        libxml2 python3 python3-venv unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# The toolchains, installed by the same script as native installs. rustup verifies the Rust
# release; cbindgen and bindgen are built from crates.io with --locked. The archives are
# dropped afterwards.
COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh rust --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads /opt/codecoremorph/toolchains/rust/cargo/registry \
    && chmod -R a+rwX /opt/codecoremorph/toolchains/rust

# The libclang binding for the C++ index helper, as in images/cpp.
COPY images/cpp/requirements.txt /opt/codecoremorph/requirements.txt
RUN python3 -m venv /opt/codecoremorph/venv \
    && /opt/codecoremorph/venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/codecoremorph/requirements.txt

# Commands get both toolchains through toolchains/rust.env (applied by the Docker executor).
# Cargo writes its locks and caches under CARGO_HOME, so the Rust homes are writable by the
# unprivileged user commands run as.
ENV PATH=/opt/codecoremorph/venv/bin:$PATH
LABEL org.codecoremorph.language="rust" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/rust.env"
USER 1000:1000
WORKDIR /workspace
