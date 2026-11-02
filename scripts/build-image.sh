#!/bin/sh
# Build a CodeCoreMorph toolchain image locally (SPEC §7.8, D-23).
# Windows twin: scripts/build-image.bat (same flags, messages and exit codes).
set -u

CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
. "$CCM_SCRIPTS_DIR/lib/common.sh"
CCM_REPO=$(cd "$CCM_SCRIPTS_DIR/.." && pwd)

usage() {
    cat <<'EOF'
Usage: build-image [--dry-run] [--help] LANGUAGE

Builds the toolchain image for LANGUAGE from images/LANGUAGE/Dockerfile and tags it
codecoremorph-LANGUAGE:local. CodeCoreMorph uses that local build until a published
image is pinned in images/LANGUAGE/image.lock. The build downloads the pinned tools
inside Docker, with the same script and manifest as native installs.

Options:
  --dry-run  print the docker command; build nothing
  --help     show this help

Exit codes: 0 success, 1 build failure, 2 usage error, 3 Docker is missing or stopped
EOF
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
# shellcheck disable=SC2086 # split the positional arguments into words
set -- $CCM_POSITIONAL
[ $# -eq 1 ] || ccm_die "$CCM_EXIT_USAGE" "expected one language, for example: build-image cpp"
language=$1
dockerfile="$CCM_REPO/images/$language/Dockerfile"
[ -f "$dockerfile" ] || ccm_die "$CCM_EXIT_USAGE" "there is no image definition for '$language'"
tag="codecoremorph-$language:local"

if [ "$CCM_DRY_RUN" = 1 ]; then
    ccm_log "would run: docker build -f $dockerfile -t $tag $CCM_REPO"
    exit 0
fi
ccm_require_cmd docker "Install Docker (Docker Desktop on Windows and macOS), then open a new" \
    "terminal."
docker info >/dev/null 2>&1 ||
    ccm_die "$CCM_EXIT_PREREQ" "Docker is installed but its daemon isn't reachable; start Docker" \
        "and try again."
ccm_log "building $tag (this downloads the pinned toolchain inside Docker)"
docker build -f "$dockerfile" -t "$tag" "$CCM_REPO" ||
    ccm_die "$CCM_EXIT_DOWNLOAD" "docker build failed for $tag"
ccm_log "built $tag"
