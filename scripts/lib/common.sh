# shellcheck shell=sh
# shellcheck disable=SC2034 # CCM_* variables are read by the scripts that source this file
# Shared helpers for CodeCoreMorph's dependency scripts (SPEC §7.15).
#
# Source this file after setting CCM_SCRIPTS_DIR to the scripts/ directory:
#   CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
#   . "$CCM_SCRIPTS_DIR/lib/common.sh"
#
# scripts/lib/common.bat implements the same functions, flags, messages and exit codes for
# cmd.exe. Change both files together.
#
# Exit codes: 0 success, 1 download or checksum failure, 2 usage error,
#             3 missing system prerequisite (the message says how to install it).

CCM_EXIT_DOWNLOAD=1
CCM_EXIT_USAGE=2
CCM_EXIT_PREREQ=3
CCM_DOWNLOAD_ATTEMPTS=4 # the first try plus 3 retries
_ccm_cr=$(printf '\r')

ccm_log() {
    printf 'codecoremorph: %s\n' "$*"
}

ccm_warn() {
    printf 'codecoremorph: warning: %s\n' "$*" >&2
}

# ccm_die CODE MESSAGE: print an error and exit the script.
ccm_die() {
    _ccm_code=$1
    shift
    printf 'codecoremorph: error: %s\n' "$*" >&2
    exit "$_ccm_code"
}

ccm_have() {
    command -v "$1" >/dev/null 2>&1
}

# ccm_require_cmd NAME HINT...: exit 3 with the install hint unless NAME is on PATH.
ccm_require_cmd() {
    _ccm_name=$1
    shift
    ccm_have "$_ccm_name" || ccm_die "$CCM_EXIT_PREREQ" "required tool '$_ccm_name' was not found. $*"
}

# ccm_parse_args ARGS...: parse the common flags. Sets CCM_PREFIX, CCM_OFFLINE, CCM_FORCE,
# CCM_DRY_RUN, CCM_HELP and CCM_POSITIONAL (space-separated).
ccm_parse_args() {
    CCM_PREFIX=""
    CCM_OFFLINE=""
    CCM_FORCE=0
    CCM_DRY_RUN=0
    CCM_HELP=0
    CCM_POSITIONAL=""
    while [ $# -gt 0 ]; do
        case $1 in
            --prefix)
                [ $# -ge 2 ] && [ -n "$2" ] || ccm_die "$CCM_EXIT_USAGE" "--prefix needs a directory"
                CCM_PREFIX=$2
                shift 2
                ;;
            --prefix=*)
                CCM_PREFIX=${1#--prefix=}
                [ -n "$CCM_PREFIX" ] || ccm_die "$CCM_EXIT_USAGE" "--prefix needs a directory"
                shift
                ;;
            --offline)
                [ $# -ge 2 ] && [ -n "$2" ] || ccm_die "$CCM_EXIT_USAGE" "--offline needs a directory"
                CCM_OFFLINE=$2
                shift 2
                ;;
            --offline=*)
                CCM_OFFLINE=${1#--offline=}
                [ -n "$CCM_OFFLINE" ] || ccm_die "$CCM_EXIT_USAGE" "--offline needs a directory"
                shift
                ;;
            --force)
                CCM_FORCE=1
                shift
                ;;
            --dry-run)
                CCM_DRY_RUN=1
                shift
                ;;
            -h | --help)
                CCM_HELP=1
                shift
                ;;
            -*)
                ccm_die "$CCM_EXIT_USAGE" "unknown option '$1' (see --help)"
                ;;
            *)
                CCM_POSITIONAL="$CCM_POSITIONAL $1"
                shift
                ;;
        esac
    done
}

# ccm_no_offline REASON...: exit 2 if --offline was given to a script that needs the network.
ccm_no_offline() {
    [ -z "${CCM_OFFLINE:-}" ] || ccm_die "$CCM_EXIT_USAGE" "--offline doesn't apply here: $*"
}

# ccm_to_posix PATH: print PATH, converting a Windows path (C:\x or C:/x) under Git Bash.
ccm_to_posix() {
    case $1 in
        [A-Za-z]:*)
            if ccm_have cygpath; then
                cygpath -u "$1"
                return
            fi
            ;;
    esac
    printf '%s\n' "$1"
}

# Sets CCM_PLATFORM to one of the manifest platforms (windows-x64, linux-arm64, ...).
ccm_detect_platform() {
    _ccm_os=$(uname -s 2>/dev/null) || _ccm_os=unknown
    _ccm_arch=$(uname -m 2>/dev/null) || _ccm_arch=unknown
    case $_ccm_os in
        Linux) _ccm_os=linux ;;
        Darwin) _ccm_os=macos ;;
        MINGW* | MSYS* | CYGWIN*) _ccm_os=windows ;;
        *) ccm_die "$CCM_EXIT_PREREQ" "unsupported operating system '$_ccm_os'" ;;
    esac
    case $_ccm_arch in
        x86_64 | amd64) _ccm_arch=x64 ;;
        aarch64 | arm64) _ccm_arch=arm64 ;;
        *) ccm_die "$CCM_EXIT_PREREQ" "unsupported processor architecture '$_ccm_arch'" ;;
    esac
    CCM_PLATFORM="$_ccm_os-$_ccm_arch"
}

# Sets CCM_HOME: --prefix, else CODECOREMORPH_HOME, else the platform default. Mirrors
# codecoremorph.core.paths: relative values resolve against the current directory.
ccm_resolve_home() {
    if [ -n "$CCM_PREFIX" ]; then
        _ccm_home=$CCM_PREFIX
    elif [ -n "${CODECOREMORPH_HOME:-}" ]; then
        _ccm_home=$CODECOREMORPH_HOME
    else
        case $CCM_PLATFORM in
            windows-*) _ccm_home="${LOCALAPPDATA:-$HOME/AppData/Local}/codecoremorph" ;;
            macos-*) _ccm_home="$HOME/Library/Caches/codecoremorph" ;;
            *)
                case ${XDG_CACHE_HOME:-} in
                    /*) _ccm_home="$XDG_CACHE_HOME/codecoremorph" ;;
                    *) _ccm_home="$HOME/.cache/codecoremorph" ;; # the XDG spec ignores relative values
                esac
                ;;
        esac
    fi
    case $_ccm_home in
        \~) _ccm_home=$HOME ;;
        \~/*) _ccm_home="$HOME/${_ccm_home#\~/}" ;;
    esac
    _ccm_home=$(ccm_to_posix "$_ccm_home")
    case $_ccm_home in
        /*) ;;
        *) _ccm_home="$(pwd)/$_ccm_home" ;;
    esac
    CCM_HOME=${_ccm_home%/}
}

# Sets CCM_PLATFORM, CCM_HOME and CCM_MANIFEST, and makes CCM_OFFLINE absolute. Call after
# ccm_parse_args.
ccm_setup() {
    ccm_detect_platform
    ccm_resolve_home
    if [ -n "${CCM_OFFLINE:-}" ]; then
        _ccm_off=$(ccm_to_posix "$CCM_OFFLINE")
        case $_ccm_off in
            /*) ;;
            *) _ccm_off="$(pwd)/$_ccm_off" ;;
        esac
        [ -d "$_ccm_off" ] || ccm_die "$CCM_EXIT_USAGE" "--offline: $CCM_OFFLINE is not a directory"
        CCM_OFFLINE=${_ccm_off%/}
    fi
    if [ -n "${CODECOREMORPH_MANIFEST:-}" ]; then
        CCM_MANIFEST=$(ccm_to_posix "$CODECOREMORPH_MANIFEST")
    else
        CCM_MANIFEST="$CCM_SCRIPTS_DIR/deps/manifest.txt"
    fi
    [ -f "$CCM_MANIFEST" ] || ccm_die "$CCM_EXIT_USAGE" "dependency manifest not found: $CCM_MANIFEST"
}

# ccm_manifest_row GROUP TOOL: find the row for this platform (or 'any').
# Sets CCM_ROW_VERSION, CCM_ROW_URL, CCM_ROW_SHA256, CCM_ROW_ARCHIVE and CCM_ROW_BIN_DIR.
ccm_manifest_row() {
    CCM_ROW_VERSION=""
    while IFS='|' read -r _g _t _v _p _u _s _a _b || [ -n "$_g" ]; do
        case $_g in '' | '#'*) continue ;; esac
        _b=${_b%"$_ccm_cr"}
        if [ "$_g" = "$1" ] && [ "$_t" = "$2" ] && { [ "$_p" = "$CCM_PLATFORM" ] || [ "$_p" = any ]; }; then
            CCM_ROW_VERSION=$_v
            CCM_ROW_URL=$_u
            CCM_ROW_SHA256=$_s
            CCM_ROW_ARCHIVE=$_a
            CCM_ROW_BIN_DIR=$_b
            break
        fi
    done <"$CCM_MANIFEST"
    [ -n "$CCM_ROW_VERSION" ] || ccm_die "$CCM_EXIT_PREREQ" \
        "the manifest has no pinned download of $2 for $CCM_PLATFORM; install $2 yourself"
}

# ccm_has_row GROUP TOOL: whether the manifest pins a download of TOOL for this platform (a
# tool its #!platforms line leaves out has none).
ccm_has_row() {
    while IFS='|' read -r _g _t _v _p _rest || [ -n "$_g" ]; do
        case $_g in '' | '#'*) continue ;; esac
        if [ "$_g" = "$1" ] && [ "$_t" = "$2" ] && { [ "$_p" = "$CCM_PLATFORM" ] || [ "$_p" = any ]; }; then
            return 0
        fi
    done <"$CCM_MANIFEST"
    return 1
}

# Exit 3 unless a downloader (not needed with --offline), a SHA-256 tool and tar are available.
ccm_require_download_tools() {
    if [ -z "${CCM_OFFLINE:-}" ] && ! ccm_have curl && ! ccm_have wget; then
        ccm_die "$CCM_EXIT_PREREQ" "neither curl nor wget was found. Install curl with your" \
            "package manager (for example: sudo apt install curl)."
    fi
    if ! ccm_have sha256sum && ! ccm_have shasum; then
        ccm_die "$CCM_EXIT_PREREQ" "neither sha256sum nor shasum was found. Install GNU" \
            "coreutils with your package manager (for example: sudo apt install coreutils)."
    fi
    ccm_require_cmd tar "Install tar with your package manager (for example: sudo apt install tar)."
}

# ccm_sha256 FILE: print the file's SHA-256 in lowercase hex.
ccm_sha256() {
    if ccm_have sha256sum; then
        _ccm_out=$(sha256sum <"$1") || return 1
    else
        _ccm_out=$(shasum -a 256 <"$1") || return 1
    fi
    printf '%s\n' "${_ccm_out%% *}"
}

# ccm_download URL DEST: download over HTTPS (or copy a file:// URL), retrying failures.
ccm_download() {
    case $1 in
        https://* | file://*) ;;
        *) ccm_die "$CCM_EXIT_DOWNLOAD" "refusing to download from a non-HTTPS URL: $1" ;;
    esac
    _ccm_try=1
    while :; do
        if ccm_have curl; then
            curl -fsSL --proto '=https,file' --connect-timeout 30 -o "$2" "$1" && return 0
        else
            case $1 in
                file://*) cp "${1#file://}" "$2" && return 0 ;;
                *) wget -q -O "$2" "$1" && return 0 ;;
            esac
        fi
        rm -f "$2"
        [ "$_ccm_try" -lt "$CCM_DOWNLOAD_ATTEMPTS" ] || return 1
        ccm_warn "download failed (attempt $_ccm_try of $CCM_DOWNLOAD_ATTEMPTS); retrying"
        _ccm_try=$((_ccm_try + 1))
        sleep 2
    done
}

# ccm_offline_file EXT: print the current manifest row's archive in CCM_OFFLINE, under its
# cache name (SHA256.EXT, as in a CODECOREMORPH_HOME's downloads directory) or its download
# name (the URL's last part). Fails when neither is there.
ccm_offline_file() {
    for _ccm_cand in "$CCM_OFFLINE/$CCM_ROW_SHA256.$1" "$CCM_OFFLINE/${CCM_ROW_URL##*/}"; do
        if [ -f "$_ccm_cand" ]; then
            printf '%s\n' "$_ccm_cand"
            return 0
        fi
    done
    return 1
}

# ccm_extract ARCHIVE TYPE DEST_DIR URL [PATTERN...]: extract everything, or only the
# members matching the shell-style PATTERNs.
ccm_extract() {
    _ccm_x_archive=$1
    _ccm_x_type=$2
    _ccm_x_dest=$3
    _ccm_x_url=$4
    shift 4
    case $_ccm_x_type in
        tar.gz | tar.xz)
            _ccm_x_flag=z
            [ "$_ccm_x_type" = tar.gz ] || _ccm_x_flag=J
            if [ $# -gt 0 ] && tar --version 2>/dev/null | grep -q GNU; then
                tar "-x${_ccm_x_flag}f" "$_ccm_x_archive" -C "$_ccm_x_dest" --wildcards "$@"
            else
                tar "-x${_ccm_x_flag}f" "$_ccm_x_archive" -C "$_ccm_x_dest" "$@"
            fi
            ;;
        zip)
            if ccm_have unzip; then
                unzip -q "$_ccm_x_archive" "$@" -d "$_ccm_x_dest"
            elif ccm_have bsdtar; then
                bsdtar -xf "$_ccm_x_archive" -C "$_ccm_x_dest" "$@"
            else
                ccm_die "$CCM_EXIT_PREREQ" "unzip is required to extract $_ccm_x_archive. Install" \
                    "it with your package manager (for example: sudo apt install unzip)."
            fi
            ;;
        file) cp "$_ccm_x_archive" "$_ccm_x_dest/${_ccm_x_url##*/}" ;;
        *) ccm_die "$CCM_EXIT_USAGE" "unknown archive type '$_ccm_x_type' in the manifest" ;;
    esac
}

# ccm_native_path PATH: print PATH as native programs see it. Under Git Bash that is the
# Windows form with forward slashes (E:/x/y), which CMake and compilers accept.
ccm_native_path() {
    case $CCM_PLATFORM in
        windows-*)
            if ccm_have cygpath; then
                cygpath -m "$1"
                return
            fi
            ;;
    esac
    printf '%s\n' "$1"
}

# ccm_install_tool GROUP TOOL [PATTERN...]: install a pinned tool from the manifest, unless
# the same version and hash is already installed. PATTERNs extract only the matching archive
# members (shell-style, e.g. '*/bin/clang'); the marker records them, so a different selection
# reinstalls. Sets CCM_TOOL_DIR and CCM_TOOL_BIN_DIR.
ccm_install_tool() {
    _ccm_group=$1
    _ccm_tool=$2
    shift 2
    _ccm_members="members:"
    [ $# -eq 0 ] || _ccm_members="members: $*"
    set -- "$_ccm_group" "$_ccm_tool" "$@"
    ccm_manifest_row "$1" "$2"
    case $1 in
        dev) _ccm_root="$CCM_HOME/tools/$2" ;;
        *) _ccm_root="$CCM_HOME/toolchains/$2" ;;
    esac
    CCM_TOOL_DIR="$_ccm_root/$CCM_ROW_VERSION"
    if [ "$CCM_ROW_BIN_DIR" = . ]; then
        CCM_TOOL_BIN_DIR=$CCM_TOOL_DIR
    else
        CCM_TOOL_BIN_DIR="$CCM_TOOL_DIR/$CCM_ROW_BIN_DIR"
    fi

    if [ "$CCM_FORCE" != 1 ] && [ -f "$CCM_TOOL_DIR/.installed" ]; then
        _ccm_marker=""
        _ccm_marker_members=""
        {
            read -r _ccm_marker || true
            read -r _ccm_marker_members || true
        } <"$CCM_TOOL_DIR/.installed"
        # The .bat twin writes CRLF lines. Markers written before member selection have one line.
        _ccm_marker=${_ccm_marker%"$_ccm_cr"}
        _ccm_marker_members=${_ccm_marker_members%"$_ccm_cr"}
        [ -n "$_ccm_marker_members" ] || _ccm_marker_members="members:"
        if [ "$_ccm_marker" = "$CCM_ROW_SHA256" ] && [ "$_ccm_marker_members" = "$_ccm_members" ]; then
            ccm_log "$2 $CCM_ROW_VERSION is already installed in $CCM_TOOL_DIR"
            return 0
        fi
    fi

    _ccm_ext=$CCM_ROW_ARCHIVE
    [ "$_ccm_ext" != file ] || _ccm_ext=bin
    _ccm_archive="$CCM_HOME/downloads/$CCM_ROW_SHA256.$_ccm_ext"
    _ccm_missing="$2 $CCM_ROW_VERSION is not in ${CCM_OFFLINE:-}: put ${CCM_ROW_URL##*/} there"
    _ccm_missing="$_ccm_missing (from $CCM_ROW_URL), or $CCM_ROW_SHA256.$_ccm_ext from the"
    _ccm_missing="$_ccm_missing downloads directory of a CODECOREMORPH_HOME on $CCM_PLATFORM"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        if [ -z "${CCM_OFFLINE:-}" ]; then
            ccm_log "would download $CCM_ROW_URL"
        elif _ccm_src=$(ccm_offline_file "$_ccm_ext"); then
            ccm_log "would use $_ccm_src"
        else
            ccm_warn "$_ccm_missing"
        fi
        ccm_log "would install $2 $CCM_ROW_VERSION into $CCM_TOOL_DIR"
        return 0
    fi

    ccm_require_download_tools
    mkdir -p "$CCM_HOME/downloads" "$_ccm_root" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not create directories under $CCM_HOME"

    if [ -f "$_ccm_archive" ] && [ "$(ccm_sha256 "$_ccm_archive")" = "$CCM_ROW_SHA256" ]; then
        ccm_log "using cached $_ccm_archive"
    else
        rm -f "$_ccm_archive"
        _ccm_part="$_ccm_archive.part"
        if [ -n "${CCM_OFFLINE:-}" ]; then
            _ccm_src=$(ccm_offline_file "$_ccm_ext") || ccm_die "$CCM_EXIT_DOWNLOAD" "$_ccm_missing"
            ccm_log "copying $2 $CCM_ROW_VERSION from $_ccm_src"
            cp "$_ccm_src" "$_ccm_part" || ccm_die "$CCM_EXIT_DOWNLOAD" "could not copy $_ccm_src"
            _ccm_what=$_ccm_src
            _ccm_fate="It is not the pinned file; replace it."
        else
            ccm_log "downloading $2 $CCM_ROW_VERSION from $CCM_ROW_URL"
            ccm_download "$CCM_ROW_URL" "$_ccm_part" ||
                ccm_die "$CCM_EXIT_DOWNLOAD" "download failed: $CCM_ROW_URL"
            _ccm_what=$CCM_ROW_URL
            _ccm_fate="The file was deleted."
        fi
        _ccm_actual=$(ccm_sha256 "$_ccm_part")
        if [ "$_ccm_actual" != "$CCM_ROW_SHA256" ]; then
            rm -f "$_ccm_part"
            ccm_die "$CCM_EXIT_DOWNLOAD" "checksum mismatch for $_ccm_what: expected" \
                "$CCM_ROW_SHA256, got $_ccm_actual. $_ccm_fate"
        fi
        mv -f "$_ccm_part" "$_ccm_archive"
    fi

    # Extract next to the destination, then rename: an interrupted install never leaves a
    # half-installed tool behind.
    _ccm_tmp="$_ccm_root/.tmp-$CCM_ROW_VERSION-$$"
    rm -rf "$_ccm_tmp"
    mkdir -p "$_ccm_tmp"
    shift 2
    if ! ccm_extract "$_ccm_archive" "$CCM_ROW_ARCHIVE" "$_ccm_tmp" "$CCM_ROW_URL" "$@"; then
        rm -rf "$_ccm_tmp"
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not extract $_ccm_archive"
    fi
    printf '%s\n%s\n' "$CCM_ROW_SHA256" "$_ccm_members" >"$_ccm_tmp/.installed"
    rm -rf "$CCM_TOOL_DIR"
    mv "$_ccm_tmp" "$CCM_TOOL_DIR" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not move the install into $CCM_TOOL_DIR"
    ccm_log "installed $_ccm_tool $CCM_ROW_VERSION into $CCM_TOOL_DIR"
}

# ccm_version_number VERSION: print major*1000000 + minor*1000 + patch.
ccm_version_number() {
    _ccm_ifs=$IFS
    IFS=.
    # shellcheck disable=SC2086 # splitting on dots is the point
    set -- $1
    IFS=$_ccm_ifs
    _ccm_n=0
    for _ccm_part in "${1:-0}" "${2:-0}" "${3:-0}"; do
        _ccm_part=${_ccm_part%%[!0-9]*}         # "0-rc1" -> "0"
        _ccm_part=${_ccm_part#"${_ccm_part%%[!0]*}"} # strip leading zeros (no octal)
        _ccm_n=$((_ccm_n * 1000 + ${_ccm_part:-0}))
    done
    printf '%s\n' "$_ccm_n"
}

# ccm_version_ge VERSION MINIMUM: succeed if VERSION >= MINIMUM.
ccm_version_ge() {
    [ "$(ccm_version_number "$1")" -ge "$(ccm_version_number "$2")" ]
}
