# CodeCoreMorph Java toolchain image (SPEC §11.2, D-63).
#
# A Temurin JDK for each Java standard (8, 11, 17, 21, 25), Maven, the Spoon jar, and a Maven
# repository with OpenRewrite and the plugins builds run offline, all installed by
# scripts/install-toolchain.sh java from the pinned manifest, as native installs are. Build it
# from the repository root with scripts/build-image.sh java (.bat on Windows).
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# The toolchain, installed by the same script as native installs. Downloads are verified
# against the manifest, and Maven checks what it downloads; the archives are dropped afterwards.
COPY scripts/install-toolchain.sh /opt/codecoremorph/scripts/install-toolchain.sh
COPY scripts/lib/common.sh /opt/codecoremorph/scripts/lib/common.sh
COPY scripts/deps/manifest.txt /opt/codecoremorph/scripts/deps/manifest.txt
RUN sh /opt/codecoremorph/scripts/install-toolchain.sh java --prefix /opt/codecoremorph \
    && rm -rf /opt/codecoremorph/downloads

# Commands get the toolchain through toolchains/java.env (applied by the Docker executor). The
# tools repository is read-only to builds: each run keeps its own artifacts in its cache.
LABEL org.codecoremorph.language="java" \
      org.codecoremorph.toolchain-env="/opt/codecoremorph/toolchains/java.env"
USER 1000:1000
WORKDIR /workspace
