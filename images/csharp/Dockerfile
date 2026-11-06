# CodeCoreMorph C# toolchain image (SPEC §11.3, D-65).
#
# The .NET SDK (with dotnet format and Roslyn) and the .NET 8 and 6 runtimes beside its own, in
# one dotnet root, installed by scripts/install-toolchain.sh csharp from the pinned manifest, as
# native installs are. Build it from the repository root with scripts/build-image.sh csharp
# (.bat on Windows).
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

# What .NET needs on Ubuntu 22.04: ICU (globalization), OpenSSL, GSSAPI, zlib and time zones;
# libc, libgcc and libstdc++ come with the base image.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libicu70 libssl3 \
        libgssapi-krb5-2 zlib1g tzdata \
    && rm -rf /var/lib/apt/lists/*

# The toolchain, installed by the same script as native installs. Downloads are verified
# against the manifest; the archives are dropped afterwards.
COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh csharp --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads /opt/codecoremorph/toolchains/dotnet-runtime8 \
        /opt/codecoremorph/toolchains/dotnet-runtime6

# Commands get the toolchain through toolchains/csharp.env (applied by the Docker executor).
# NuGet's packages, the .NET home and the HTTP cache are each run's own, in its cache.
LABEL org.codecoremorph.language="csharp" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/csharp.env"
USER 1000:1000
WORKDIR /workspace
