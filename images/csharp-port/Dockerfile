# CodeCoreMorph toolchain image for ports from C# (SPEC §11.9, §11.10, D-77, D-78).
#
# A port from C# builds the new code (C++ with CMake, or Rust with Cargo in Phase 18) and the C#
# with the .NET SDK in one run, the C# tests loading the new code through P/Invoke. So this image
# holds the Rust image's toolchains (Rust and C++) and the C# image's .NET SDK, installed by
# scripts/install-toolchain.sh rust and csharp from the pinned manifest, as native installs are.
# Build it from the repository root with scripts/build-image.sh csharp-port (.bat on Windows).
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

# The Rust image's packages (images/rust), and what .NET needs on Ubuntu 22.04 (images/csharp).
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential ca-certificates ccache curl libtinfo5 libtool \
        libxml2 python3 python3-venv unzip xz-utils \
        libicu70 libssl3 libgssapi-krb5-2 zlib1g tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh rust --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads /opt/codecoremorph/toolchains/rust/cargo/registry \
    && chmod -R a+rwX /opt/codecoremorph/toolchains/rust
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh csharp --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads /opt/codecoremorph/toolchains/dotnet-runtime8 \
        /opt/codecoremorph/toolchains/dotnet-runtime6

# One environment for both: the executor applies a single file, whose last PATH_PREPEND wins.
RUN cd /opt/codecoremorph/toolchains \
    && { printf '# CodeCoreMorph toolchain for ports from C#: rust.env and csharp.env, joined.\n'; \
         printf 'PATH_PREPEND=%s:%s\n' "$(sed -n 's/^PATH_PREPEND=//p' rust.env)" \
             "$(sed -n 's/^PATH_PREPEND=//p' csharp.env)"; \
         grep -h -v -e '^#' -e '^PATH_PREPEND=' -e '^$' rust.env csharp.env; } >csharp-port.env

# The libclang binding for the C++ index helper and the bridge declarations, as in images/cpp.
COPY images/cpp/requirements.txt /opt/codecoremorph/requirements.txt
RUN python3 -m venv /opt/codecoremorph/venv \
    && /opt/codecoremorph/venv/bin/pip install --no-cache-dir --require-hashes \
        -r /opt/codecoremorph/requirements.txt

ENV PATH=/opt/codecoremorph/venv/bin:$PATH
LABEL org.codecoremorph.language="csharp-port" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/csharp-port.env"
USER 1000:1000
WORKDIR /workspace
