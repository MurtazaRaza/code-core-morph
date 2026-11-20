# CodeCoreMorph toolchain image for ports from Java (SPEC §11.12, D-91, D-92).
#
# A port from Java builds the new code (C++ with CMake, or Rust with Cargo) and the Java with
# Maven in one run, the Java tests loading the new code through JNI. So this image holds the
# Rust image's toolchains (Rust and C++) and the Java image's JDKs, Maven and tools repository,
# installed by scripts/install-toolchain.sh rust and java from the pinned manifest, as native
# installs are. Build it from the repository root with scripts/build-image.sh java-port (.bat on
# Windows).
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

# The Rust image's packages (images/rust) and the Java image's (images/java).
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential ca-certificates ccache curl libtinfo5 libtool \
        libxml2 python3 python3-venv unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh rust --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads /opt/codecoremorph/toolchains/rust/cargo/registry \
    && chmod -R a+rwX /opt/codecoremorph/toolchains/rust
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh java --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads

# One environment for both: the executor applies a single file, whose last PATH_PREPEND wins.
RUN cd /opt/codecoremorph/toolchains \
    && { printf '# CodeCoreMorph toolchain for ports from Java: rust.env and java.env, joined.\n'; \
         printf 'PATH_PREPEND=%s:%s\n' "$(sed -n 's/^PATH_PREPEND=//p' rust.env)" \
             "$(sed -n 's/^PATH_PREPEND=//p' java.env)"; \
         grep -h -v -e '^#' -e '^PATH_PREPEND=' -e '^$' rust.env java.env; } >java-port.env

# The libclang binding for the C++ index helper and the bridge declarations, as in images/cpp.
COPY images/cpp/requirements.txt /opt/codecoremorph/requirements.txt
RUN python3 -m venv /opt/codecoremorph/venv \
    && /opt/codecoremorph/venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/codecoremorph/requirements.txt

ENV PATH=/opt/codecoremorph/venv/bin:$PATH
LABEL org.codecoremorph.language="java-port" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/java-port.env"
USER 1000:1000
WORKDIR /workspace
