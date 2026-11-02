#!/bin/sh
# Pre-pull the pinned CodeCoreMorph toolchain images (SPEC §7.15). Optional: runs pull images
# on demand. Windows twin: scripts/pull-images.bat (same flags, messages and exit codes).
set -u

CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
. "$CCM_SCRIPTS_DIR/lib/common.sh"
CCM_IMAGES=$(cd "$CCM_SCRIPTS_DIR/.." && pwd)/images
[ -d "$CCM_SCRIPTS_DIR/../_images" ] && CCM_IMAGES=$(cd "$CCM_SCRIPTS_DIR/../_images" && pwd)

usage() {
    cat <<'EOF'
Usage: pull-images [--dry-run] [--help] [LANGUAGE...]

Pulls the toolchain image of each LANGUAGE (default: every language with an
images/LANGUAGE/image.lock), pinned by digest. An image that isn't published yet
is skipped with a note on how to build it locally.

Options:
  --dry-run  print what would be pulled; pull nothing
  --help     show this help

Exit codes: 0 success, 1 pull failure, 2 usage error, 3 Docker is missing or stopped
EOF
}

# pull_one LANGUAGE
pull_one() {
    _lock="$CCM_IMAGES/$1/image.lock"
    [ -f "$_lock" ] || ccm_die "$CCM_EXIT_USAGE" "there is no image for '$1'"
    _image=""
    _digest=""
    while IFS='=' read -r _key _value || [ -n "$_key" ]; do
        _value=${_value%"$_ccm_cr"}
        case $_key in
            image) _image=$_value ;;
            digest) _digest=$_value ;;
        esac
    done <"$_lock"
    if [ -z "$_digest" ]; then
        ccm_warn "$1: $_image isn't published yet; build it locally with: build-image $1"
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would pull $_image@$_digest"
        return 0
    fi
    ccm_log "pulling $_image@$_digest"
    docker pull "$_image@$_digest" || ccm_die "$CCM_EXIT_DOWNLOAD" "could not pull $_image@$_digest"
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
if [ "$CCM_DRY_RUN" != 1 ]; then
    ccm_require_cmd docker "Install Docker (Docker Desktop on Windows and macOS), then open a" \
        "new terminal."
    docker info >/dev/null 2>&1 ||
        ccm_die "$CCM_EXIT_PREREQ" "Docker is installed but its daemon isn't reachable; start" \
            "Docker and try again."
fi
languages=$CCM_POSITIONAL
if [ -z "$languages" ]; then
    for _dir in "$CCM_IMAGES"/*/; do
        [ -f "$_dir/image.lock" ] && languages="$languages $(basename "$_dir")"
    done
fi
for language in $languages; do
    pull_one "$language"
done
ccm_log "done"
