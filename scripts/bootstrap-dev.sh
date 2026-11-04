#!/bin/sh
# Contributor setup for CodeCoreMorph (SPEC §7.15).
# Windows twin: scripts/bootstrap-dev.bat (same flags, messages and exit codes).
set -u

CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
. "$CCM_SCRIPTS_DIR/lib/common.sh"
CCM_REPO=$(cd "$CCM_SCRIPTS_DIR/.." && pwd)

UV_MIN_VERSION=0.12.0
NODE_MIN_VERSION=24.15.0 # dashboard-ui's test tools (jsdom) need it
GIT_MIN_VERSION=2.30.0
PYTHON_VERSION=3.12

usage() {
    cat <<'EOF'
Usage: bootstrap-dev [--prefix DIR] [--offline DIR] [--force] [--dry-run] [--help]

Sets up a CodeCoreMorph development environment: uv (from PATH, or a pinned,
checksum-verified download), Python 3.12, the project's dependencies, the
pre-commit hooks and Node.js for the dashboard's web UI (from PATH, or a pinned
download). Then it checks git and Docker. No admin rights needed.

Options:
  --prefix DIR   where downloaded tools go (default: CODECOREMORPH_HOME)
  --offline DIR  take uv and Node.js from the archives in DIR, and let uv use
                 only its cache (UV_OFFLINE=1); see install-toolchain --help
  --force        reinstall downloaded tools
  --dry-run      print what would be downloaded and run; change nothing
  --help         show this help

Exit codes: 0 success, 1 download or checksum failure, 2 usage error,
            3 missing system prerequisite
EOF
}

# run_step DESCRIPTION COMMAND...: run a setup step, or describe it in a dry run.
run_step() {
    _desc=$1
    shift
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would run: $_desc"
        return 0
    fi
    ccm_log "running: $_desc"
    "$@" || ccm_die "$CCM_EXIT_DOWNLOAD" "'$_desc' failed with exit code $?"
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
[ -z "$CCM_POSITIONAL" ] || ccm_die "$CCM_EXIT_USAGE" "unexpected argument:$CCM_POSITIONAL"
ccm_setup
if [ -n "$CCM_OFFLINE" ]; then
    # uv installs Python and the dependencies itself: offline, from its cache only.
    UV_OFFLINE=1
    export UV_OFFLINE
    ccm_log "offline: uv uses only its cache (UV_OFFLINE=1)"
fi

# 1. git is a system prerequisite: detect it, never install it.
ccm_require_cmd git "Install git >= $GIT_MIN_VERSION with your package manager (for example:" \
    "sudo apt install git, or xcode-select --install on macOS), then open a new terminal."
# shellcheck disable=SC2046 # split "git version X.Y.Z" into words
set -- $(git --version)
git_version=${3:-0}
ccm_version_ge "$git_version" "$GIT_MIN_VERSION" ||
    ccm_die "$CCM_EXIT_PREREQ" "git $git_version is too old; CodeCoreMorph needs" \
        "$GIT_MIN_VERSION or later. Upgrade it with your package manager."
ccm_log "git $git_version found"

# 2. uv: use the one on PATH if it's recent enough, else the pinned download.
uv=""
if ccm_have uv; then
    # shellcheck disable=SC2046 # split "uv X.Y.Z (...)" into words
    set -- $(uv --version)
    uv_version=${2:-0}
    if ccm_version_ge "$uv_version" "$UV_MIN_VERSION"; then
        uv=uv
        ccm_log "using uv $uv_version from PATH"
    else
        ccm_log "uv $uv_version on PATH is older than $UV_MIN_VERSION; using the pinned version"
    fi
fi
if [ -z "$uv" ]; then
    ccm_install_tool dev uv
    uv="$CCM_TOOL_BIN_DIR/uv"
fi

# 3-6. Python, dependencies, hooks, smoke test. All run from the repository root.
cd "$CCM_REPO" || ccm_die "$CCM_EXIT_USAGE" "can't enter $CCM_REPO"
run_step "uv python install $PYTHON_VERSION" "$uv" python install "$PYTHON_VERSION"
run_step "uv sync" "$uv" sync
if [ "$CCM_DRY_RUN" = 1 ] || git rev-parse --git-dir >/dev/null 2>&1; then
    run_step "uv run pre-commit install" "$uv" run pre-commit install
else
    ccm_warn "not a git checkout; skipping the pre-commit hooks"
fi
run_step "uv run codecoremorph --version" "$uv" run codecoremorph --version

# 7. Node.js builds the dashboard's web UI (dashboard-ui/): the one on PATH if it's recent
#    enough, else the pinned download.
node=""
if ccm_have node; then
    node_version=$(node --version 2>/dev/null) || node_version=v0
    node_version=${node_version#v}
    if ccm_version_ge "$node_version" "$NODE_MIN_VERSION"; then
        node=node
        ccm_log "using Node.js $node_version from PATH"
    else
        ccm_log "Node.js $node_version on PATH is older than $NODE_MIN_VERSION; using the" \
            "pinned version"
    fi
fi
if [ -z "$node" ]; then
    ccm_install_tool dev node
    node="$CCM_TOOL_BIN_DIR/node"
fi

# 8. Docker is optional: without it, CodeCoreMorph runs toolchains natively.
if ! ccm_have docker; then
    ccm_warn "Docker was not found; CodeCoreMorph will use native mode. Install Docker to run" \
        "toolchains in the sandbox."
elif docker info >/dev/null 2>&1; then
    ccm_log "Docker is reachable"
else
    ccm_warn "Docker is installed but its daemon isn't reachable; start it to use the sandbox."
fi

if [ "$uv" = uv ]; then
    ccm_log "done. Try: uv run codecoremorph --help"
else
    ccm_log "done. uv was installed at $uv and not added to PATH. Add its directory to PATH" \
        "so the pre-commit hooks can run it. Then try: uv run codecoremorph --help"
fi
if [ "$node" != node ] && [ "$CCM_DRY_RUN" != 1 ]; then
    ccm_log "Node.js was installed at $node and not added to PATH. Add its directory to PATH" \
        "to build the dashboard: cd dashboard-ui; npm ci; npm run build"
fi
