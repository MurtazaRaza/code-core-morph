#!/bin/sh
# Pull the Ollama models a migration manifest's roles use (SPEC §7.15). `codecoremorph doctor`
# prints this command with the configured tags. Windows twin: scripts/pull-models.bat (same
# flags, messages and exit codes).
set -u

CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
. "$CCM_SCRIPTS_DIR/lib/common.sh"

usage() {
    cat <<'TEXT'
Usage: pull-models [--dry-run] [--help] TAG...

Checks that Ollama is installed and running, then runs `ollama pull` for each
model TAG (for example qwen3:4b). Ollama verifies each download itself and keeps
models in its own directory (set OLLAMA_MODELS before starting Ollama to move it).

Options:
  --dry-run  print what would be pulled; pull nothing
  --help     show this help

Exit codes: 0 success, 1 pull failure, 2 usage error, 3 Ollama is missing or stopped
TEXT
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
[ -n "$CCM_POSITIONAL" ] ||
    ccm_die "$CCM_EXIT_USAGE" "name at least one model tag, for example: pull-models qwen3:4b"
if [ "$CCM_DRY_RUN" != 1 ]; then
    ccm_require_cmd ollama "Install Ollama from https://ollama.com/download, then open a new" \
        "terminal."
    ollama list >/dev/null 2>&1 ||
        ccm_die "$CCM_EXIT_PREREQ" "Ollama is installed but its server isn't reachable; start" \
            "Ollama (the app, or 'ollama serve') and try again."
fi
for tag in $CCM_POSITIONAL; do
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would pull $tag"
        continue
    fi
    ccm_log "pulling $tag"
    ollama pull "$tag" || ccm_die "$CCM_EXIT_DOWNLOAD" "could not pull $tag"
done
ccm_log "done"
