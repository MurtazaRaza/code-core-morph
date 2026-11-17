#!/bin/sh
# CodeCoreMorph (D-68): installed as cc, c++ and ar for a Make build, the CC, CXX and AR it
# passes, and as ar, cc, gcc, clang, c++, g++ and clang++ in that directory's path/, first on
# the PATH, for recipes that name a tool instead of $(CC) or $(AR) (argon2's `ar rcs`). While
# CODECOREMORPH_CAPTURE names a directory, each call leaves a file there: the working
# directory, then the arguments, each ending in a NUL byte. A Cargo build's calls (rustc's
# linker, build scripts) leave none. Then the real tool runs: CODECOREMORPH_CC,
# CODECOREMORPH_CXX or CODECOREMORPH_AR, or from path/ the tool of that name further down the
# PATH. CODECOREMORPH_CC_EXTRA adds options to the CC and CXX wrappers' calls (one word each),
# such as clang's -fdiagnostics-absolute-paths, which the toolchain's compiler takes and gcc
# (or rustc's linker, cc) may not.
name=${0##*/}
extra=
here=$(cd "${0%/*}" && pwd -P) || exit 2
if [ "${here##*/}" = path ]; then
    case $name in
        cc|gcc|clang) tool='cc' ;;
        c++|g++|clang++) tool='cxx' ;;
        ar) tool='ar' ;;
        *) echo "capture.sh: installed under an unknown name: $name" >&2; exit 2 ;;
    esac
    real=
    rest=$PATH
    while [ -n "$rest" ]; do
        dir=${rest%%:*}
        case $rest in *:*) rest=${rest#*:} ;; *) rest= ;; esac
        if [ -n "$dir" ] && [ -x "$dir/$name" ] && [ ! -d "$dir/$name" ] &&
            [ "$(cd "$dir" 2>/dev/null && pwd -P)" != "$here" ]; then
            real=$dir/$name
            break
        fi
    done
    if [ -z "$real" ]; then
        echo "$name: not found" >&2
        exit 127
    fi
else
    case $name in
        cc) tool='cc'; real=${CODECOREMORPH_CC:-cc} ;;
        c++) tool='cxx'; real=${CODECOREMORPH_CXX:-c++} ;;
        ar) tool='ar'; real=${CODECOREMORPH_AR:-ar} ;;
        *) echo "capture.sh: installed under an unknown name: $name" >&2; exit 2 ;;
    esac
    extra=${CODECOREMORPH_CC_EXTRA:-}
fi
if [ -n "${CODECOREMORPH_CAPTURE:-}" ] && [ -z "${CARGO_PKG_NAME:-}" ]; then
    log=$(mktemp "$CODECOREMORPH_CAPTURE/$tool.XXXXXXXXXX") || exit 2
    { printf '%s\0' "$PWD"; printf '%s\0' "$@"; } > "$log" || exit 2
fi
if [ "$tool" = ar ]; then
    exec "$real" "$@"
fi
# shellcheck disable=SC2086 # the extra options split into words on purpose
exec "$real" "$@" $extra
