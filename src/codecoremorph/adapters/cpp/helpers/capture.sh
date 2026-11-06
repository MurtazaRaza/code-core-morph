#!/bin/sh
# CodeCoreMorph (D-68): installed as cc, c++ and ar for a Make build. While
# CODECOREMORPH_CAPTURE names a directory, each call leaves a file there: the working
# directory, then the arguments, each ending in a NUL byte. Then the real tool runs:
# CODECOREMORPH_CC, CODECOREMORPH_CXX or CODECOREMORPH_AR. CODECOREMORPH_CC_EXTRA adds
# options to compiler calls (one word each), such as clang's -fdiagnostics-absolute-paths.
case ${0##*/} in
    cc) tool='cc'; real=${CODECOREMORPH_CC:-cc} ;;
    c++) tool='cxx'; real=${CODECOREMORPH_CXX:-c++} ;;
    ar) tool='ar'; real=${CODECOREMORPH_AR:-ar} ;;
    *) echo "capture.sh: installed under an unknown name: ${0##*/}" >&2; exit 2 ;;
esac
if [ -n "${CODECOREMORPH_CAPTURE:-}" ]; then
    log=$(mktemp "$CODECOREMORPH_CAPTURE/$tool.XXXXXXXXXX") || exit 2
    { printf '%s\0' "$PWD"; printf '%s\0' "$@"; } > "$log" || exit 2
fi
if [ "$tool" = ar ]; then
    exec "$real" "$@"
fi
# shellcheck disable=SC2086 # the extra options split into words on purpose
exec "$real" "$@" ${CODECOREMORPH_CC_EXTRA:-}
