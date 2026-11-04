#!/bin/sh
# Native toolchain installer for CodeCoreMorph (SPEC §7.15).
# Windows twin: scripts/install-toolchain.bat (same flags, messages and exit codes).
set -u

CCM_SCRIPTS_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source-path=SCRIPTDIR source=lib/common.sh
. "$CCM_SCRIPTS_DIR/lib/common.sh"

usage() {
    cat <<'EOF'
Usage: install-toolchain [--prefix DIR] [--offline DIR] [--force] [--dry-run] [--help] LANGUAGE

Installs the pinned native toolchain for LANGUAGE under CODECOREMORPH_HOME, so
CodeCoreMorph can build and test projects without Docker. Every download is
checksum-verified. No admin rights are needed, and your PATH is not changed.

Languages:
  cpp   LLVM (clang, clang-tidy, clang-format; llvm-mingw on Windows), CMake,
        Ninja, and GoogleTest built with that compiler (1.12.1, plus 1.8.1
        for projects that build as C++98/03)
  rust  the cpp toolchain, plus Rust (through rustup, with clippy and
        rustfmt), cargo-nextest, cbindgen and Corrosion, for C++ to Rust
        ports; rustup and cargo download Rust and cbindgen themselves, so
        --offline doesn't apply
  all   every available language (cpp and rust)

Options:
  --prefix DIR   install under DIR (default: CODECOREMORPH_HOME)
  --offline DIR  install from the archives in DIR, without the network: files
                 named as in the manifest's URLs, or a downloads directory
                 copied from a CODECOREMORPH_HOME on the same platform
  --force        reinstall even if the pinned version is already installed
  --dry-run      print what would be downloaded and built; change nothing
  --help         show this help

CodeCoreMorph finds the tools through toolchains/LANGUAGE.env. To use them in
your own shell, run toolchains/activate-LANGUAGE (.bat or .sh).

Exit codes: 0 success, 1 download, checksum or build failure, 2 usage error,
            3 missing system prerequisite
EOF
}

# Members of the LLVM archive to extract. The full release is several GB unpacked; CodeCoreMorph
# needs the compiler, linker, clang-tidy, clang-format, their shared libraries and the
# compiler's resource directory.
llvm_members() {
    case $CCM_PLATFORM in
        macos-*) _libs='*/lib/lib*.dylib' ;;
        *) _libs='*/lib/lib*.so*' ;;
    esac
    LLVM_MEMBERS="*/bin/clang */bin/clang++ */bin/clang-[0-9]* */bin/clang-tidy */bin/clang-format"
    LLVM_MEMBERS="$LLVM_MEMBERS */bin/lld */bin/ld.lld */bin/llvm-ar */bin/llvm-ranlib */lib/clang/*"
    LLVM_MEMBERS="$LLVM_MEMBERS $_libs"
}

cpp_check_prerequisites() {
    case $CCM_PLATFORM in
        linux-*)
            if ! { ccm_have cc || ccm_have gcc; } || [ ! -d /usr/include/c++ ]; then
                ccm_die "$CCM_EXIT_PREREQ" "the C and C++ standard library headers were not" \
                    "found. Install them with your package manager, for example: sudo apt" \
                    "install build-essential (Debian, Ubuntu) or sudo dnf install gcc-c++" \
                    "(Fedora). Then run this script again."
            fi
            ccm_require_cmd xz "Install xz with your package manager (for example: sudo apt" \
                "install xz-utils)."
            ;;
        macos-x64)
            ccm_die "$CCM_EXIT_PREREQ" "LLVM publishes no macOS build for Intel processors, so" \
                "no native C++ toolchain is pinned for macos-x64. Use Docker instead" \
                "(sandbox.mode: docker)."
            ;;
        macos-*)
            xcrun --show-sdk-path >/dev/null 2>&1 ||
                ccm_die "$CCM_EXIT_PREREQ" "the Xcode Command Line Tools were not found." \
                    "Install them with: xcode-select --install"
            ;;
    esac
}

# Sets LLVM_ID, LLVM_BIN and EXTRA_BIN (runtime DLLs that test executables need on Windows).
cpp_install_compiler() {
    EXTRA_BIN=""
    case $CCM_PLATFORM in
        windows-*)
            ccm_install_tool cpp llvm-mingw
            LLVM_ID="llvm-mingw $CCM_ROW_VERSION"
            LLVM_BIN=$CCM_TOOL_BIN_DIR
            EXTRA_BIN="${LLVM_BIN%/bin}/x86_64-w64-mingw32/bin"
            ;;
        *)
            llvm_members
            set -f # the member patterns must reach tar unexpanded
            # shellcheck disable=SC2086 # one argument per pattern
            ccm_install_tool cpp llvm $LLVM_MEMBERS
            set +f
            LLVM_ID="llvm $CCM_ROW_VERSION"
            LLVM_BIN=$CCM_TOOL_BIN_DIR
            ;;
    esac
}

# cpp_build_googletest SOURCE_DIR [CMAKE_ARG...]: build and install GoogleTest with the
# downloaded tools, unless the same source was already built with the same compiler. Sets
# GTEST_PREFIX.
cpp_build_googletest() {
    _gt_src=$1
    shift
    _gt_root="$CCM_HOME/toolchains/googletest/$CCM_ROW_VERSION"
    GTEST_PREFIX="$_gt_root/install"
    _gt_stamp="$CCM_ROW_SHA256 $LLVM_ID"
    if [ "$CCM_FORCE" != 1 ] && [ -f "$GTEST_PREFIX/.built" ]; then
        _gt_have=""
        read -r _gt_have <"$GTEST_PREFIX/.built" || true
        if [ "${_gt_have%"$_ccm_cr"}" = "$_gt_stamp" ]; then
            ccm_log "GoogleTest $CCM_ROW_VERSION is already built in $GTEST_PREFIX"
            return 0
        fi
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would build GoogleTest $CCM_ROW_VERSION with $LLVM_ID into $GTEST_PREFIX"
        return 0
    fi

    _gt_build="$_gt_root/.build-$$"
    _gt_tmp="$_gt_root/.install-$$"
    _gt_log="$_gt_root/build.log"
    rm -rf "$_gt_build" "$_gt_tmp"
    # Shells differ on whether a failed redirection fails the `if ! { ... }` below.
    { mkdir -p "$_gt_root" && : >"$_gt_log"; } 2>/dev/null ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not create $_gt_log"
    ccm_log "building GoogleTest $CCM_ROW_VERSION with $LLVM_ID (log: $_gt_log)"
    if ! {
        "$CMAKE" -S "$(ccm_native_path "$_gt_src")" -B "$(ccm_native_path "$_gt_build")" \
            -G Ninja "-DCMAKE_MAKE_PROGRAM=$(ccm_native_path "$NINJA")" \
            "-DCMAKE_C_COMPILER=$(ccm_native_path "$CC_PATH")" \
            "-DCMAKE_CXX_COMPILER=$(ccm_native_path "$CXX_PATH")" \
            -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=$(ccm_native_path "$_gt_tmp")" \
            -DBUILD_GMOCK=ON -DINSTALL_GTEST=ON "$@" &&
            "$CMAKE" --build "$(ccm_native_path "$_gt_build")" &&
            "$CMAKE" --install "$(ccm_native_path "$_gt_build")"
    } >"$_gt_log" 2>&1; then
        rm -rf "$_gt_build" "$_gt_tmp"
        tail -n 20 "$_gt_log" >&2
        ccm_die "$CCM_EXIT_DOWNLOAD" "building GoogleTest failed; the full log is $_gt_log"
    fi
    printf '%s\n' "$_gt_stamp" >"$_gt_tmp/.built"
    rm -rf "$GTEST_PREFIX" "$_gt_build"
    mv "$_gt_tmp" "$GTEST_PREFIX" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not move the GoogleTest build into $GTEST_PREFIX"
    ccm_log "installed GoogleTest $CCM_ROW_VERSION into $GTEST_PREFIX"
}

# ccm_env_path PATH: a path as written to cpp.env, which native programs read (the Windows
# form with backslashes under Git Bash).
ccm_env_path() {
    case $CCM_PLATFORM in
        windows-*)
            if ccm_have cygpath; then
                cygpath -w "$1"
                return
            fi
            ;;
    esac
    printf '%s\n' "$1"
}

cpp_write_env() {
    _env="$CCM_HOME/toolchains/cpp.env"
    _activate="$CCM_HOME/toolchains/activate-cpp.sh"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would write $_env and $_activate"
        return 0
    fi
    _sep=:
    case $CCM_PLATFORM in windows-*) _sep=';' ;; esac
    _prepend="$(ccm_env_path "$LLVM_BIN")$_sep$(ccm_env_path "$CMAKE_BIN")$_sep$(ccm_env_path "$NINJA_BIN")"
    _sh_path="$LLVM_BIN:$CMAKE_BIN:$NINJA_BIN"
    if [ -n "$EXTRA_BIN" ]; then
        _prepend="$_prepend$_sep$(ccm_env_path "$EXTRA_BIN")"
        _sh_path="$_sh_path:$EXTRA_BIN"
    fi
    if ! {
        printf '# CodeCoreMorph C++ toolchain, written by install-toolchain. Do not edit.\n'
        printf '# %s, cmake %s, ninja %s, googletest %s and %s (C++98/03)\n' "$LLVM_ID" \
            "$CMAKE_VERSION" "$NINJA_VERSION" "$GTEST_VERSION" "$GTEST98_VERSION"
        printf 'PATH_PREPEND=%s\n' "$_prepend"
        printf 'CC=%s\n' "$(ccm_env_path "$CC_PATH")"
        printf 'CXX=%s\n' "$(ccm_env_path "$CXX_PATH")"
        printf 'CMAKE_GENERATOR=Ninja\n'
        printf 'CMAKE_PREFIX_PATH=%s\n' "$(ccm_env_path "$GTEST_PREFIX")"
        # The C++ adapter uses this prefix instead when a project builds as C++98/03.
        printf 'CODECOREMORPH_CXX98_PREFIX_PATH=%s\n' "$(ccm_env_path "$GTEST98_PREFIX")"
    } >"$_env.tmp" || ! mv -f "$_env.tmp" "$_env"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_env"
    fi
    if ! {
        printf '# Use the CodeCoreMorph C++ toolchain in this shell:  . "%s"\n' "$_activate"
        # shellcheck disable=SC2016 # $PATH expands when the activation script runs
        printf 'PATH="%s:$PATH"\n' "$_sh_path"
        printf 'CC="%s"\n' "$(ccm_native_path "$CC_PATH")"
        printf 'CXX="%s"\n' "$(ccm_native_path "$CXX_PATH")"
        printf 'CMAKE_GENERATOR=Ninja\n'
        printf 'CMAKE_PREFIX_PATH="%s"\n' "$(ccm_native_path "$GTEST_PREFIX")"
        printf 'export PATH CC CXX CMAKE_GENERATOR CMAKE_PREFIX_PATH\n'
    } >"$_activate.tmp" || ! mv -f "$_activate.tmp" "$_activate"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_activate"
    fi
    case $CCM_PLATFORM in
        windows-*)
            # Keep the cmd.exe activation script in step (install-toolchain.bat writes the same
            # bytes); an older one could point at a removed version.
            _bat="$CCM_HOME/toolchains/activate-cpp.bat"
            if ! {
                printf '@echo off\r\n'
                printf 'rem Use the CodeCoreMorph C++ toolchain in this cmd.exe window:  call "%s"\r\n' \
                    "$(ccm_env_path "$_bat")"
                printf 'set "PATH=%s;%%PATH%%"\r\n' "$_prepend"
                printf 'set "CC=%s"\r\n' "$(ccm_env_path "$CC_PATH")"
                printf 'set "CXX=%s"\r\n' "$(ccm_env_path "$CXX_PATH")"
                printf 'set "CMAKE_GENERATOR=Ninja"\r\n'
                printf 'set "CMAKE_PREFIX_PATH=%s"\r\n' "$(ccm_env_path "$GTEST_PREFIX")"
            } >"$_bat.tmp" || ! mv -f "$_bat.tmp" "$_bat"; then
                ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_bat"
            fi
            ;;
    esac
    ccm_log "wrote $_env"
    ccm_log "to use the toolchain in your own shell, run: . \"$_activate\""
}

install_cpp() {
    cpp_check_prerequisites
    _exe=""
    case $CCM_PLATFORM in windows-*) _exe=.exe ;; esac

    cpp_install_compiler
    CC_PATH="$LLVM_BIN/clang$_exe"
    CXX_PATH="$LLVM_BIN/clang++$_exe"
    ccm_install_tool cpp cmake
    CMAKE_VERSION=$CCM_ROW_VERSION
    CMAKE_BIN=$CCM_TOOL_BIN_DIR
    CMAKE="$CMAKE_BIN/cmake$_exe"
    ccm_install_tool cpp ninja
    NINJA_VERSION=$CCM_ROW_VERSION
    NINJA_BIN=$CCM_TOOL_BIN_DIR
    NINJA="$NINJA_BIN/ninja$_exe"
    ccm_install_tool cpp googletest
    GTEST_VERSION=$CCM_ROW_VERSION
    cpp_build_googletest "$CCM_TOOL_BIN_DIR"
    _gt_modern=$GTEST_PREFIX
    # 1.8.1 predates the CMake 3.5 policies that CMake 4 requires.
    ccm_install_tool cpp googletest-cxx98
    GTEST98_VERSION=$CCM_ROW_VERSION
    cpp_build_googletest "$CCM_TOOL_BIN_DIR" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_CXX_STANDARD=98 -DCMAKE_CXX_STANDARD_REQUIRED=ON
    GTEST98_PREFIX=$GTEST_PREFIX
    GTEST_PREFIX=$_gt_modern
    cpp_write_env
}

# The Rust release rustup installs, and the cbindgen release cargo builds (D-56). rustup and
# cargo verify what they download themselves (SPEC 7.15 rule 1).
RUST_VERSION=1.99.1
CBINDGEN_VERSION=0.29.4

# Sets RUST_HOST, RUSTUP_HOME_DIR and CARGO_HOME_DIR, and installs the pinned Rust release.
rust_install_toolchain() {
    case $CCM_PLATFORM in
        windows-*) RUST_HOST=x86_64-pc-windows-gnullvm ;; # links with llvm-mingw (D-32)
        *) RUST_HOST="" ;;
    esac
    RUSTUP_HOME_DIR="$CCM_HOME/toolchains/rust/rustup"
    CARGO_HOME_DIR="$CCM_HOME/toolchains/rust/cargo"
    ccm_install_tool rust rustup
    RUSTUP_VERSION=$CCM_ROW_VERSION
    _init="$CCM_TOOL_DIR/${CCM_ROW_URL##*/}"
    _rustc="$CARGO_HOME_DIR/bin/rustc$_exe"
    if [ "$CCM_FORCE" != 1 ] && [ -x "$_rustc" ] &&
        RUSTUP_HOME="$(ccm_native_path "$RUSTUP_HOME_DIR")" CARGO_HOME="$(ccm_native_path "$CARGO_HOME_DIR")" \
            RUSTUP_TOOLCHAIN="$RUST_VERSION" "$_rustc" --version 2>/dev/null |
        grep -q "^rustc $RUST_VERSION "; then
        ccm_log "Rust $RUST_VERSION is already installed in $CCM_HOME/toolchains/rust"
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would install Rust $RUST_VERSION with rustup $RUSTUP_VERSION into" \
            "$CCM_HOME/toolchains/rust"
        return 0
    fi
    chmod +x "$_init" 2>/dev/null || true
    set -- -y --no-modify-path --profile minimal --default-toolchain "$RUST_VERSION" \
        -c clippy -c rustfmt
    [ -z "$RUST_HOST" ] || set -- "$@" --default-host "$RUST_HOST"
    ccm_log "installing Rust $RUST_VERSION with rustup $RUSTUP_VERSION (rustup downloads and" \
        "verifies it)"
    RUSTUP_HOME="$(ccm_native_path "$RUSTUP_HOME_DIR")" CARGO_HOME="$(ccm_native_path "$CARGO_HOME_DIR")" "$_init" "$@" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "rustup-init failed to install Rust $RUST_VERSION"
}

# Builds the pinned cbindgen with cargo, unless it is already there. Sets CBINDGEN_BIN.
rust_install_cbindgen() {
    _root="$CCM_HOME/toolchains/cbindgen/$CBINDGEN_VERSION"
    CBINDGEN_BIN="$_root/bin"
    if [ "$CCM_FORCE" != 1 ] && [ -x "$CBINDGEN_BIN/cbindgen$_exe" ]; then
        ccm_log "cbindgen $CBINDGEN_VERSION is already installed in $_root"
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would build cbindgen $CBINDGEN_VERSION with cargo install --locked into $_root"
        return 0
    fi
    ccm_log "building cbindgen $CBINDGEN_VERSION (cargo install --locked)"
    PATH="$LLVM_BIN:$PATH" RUSTUP_HOME="$(ccm_native_path "$RUSTUP_HOME_DIR")" CARGO_HOME="$(ccm_native_path "$CARGO_HOME_DIR")" \
        RUSTUP_TOOLCHAIN="$RUST_VERSION" "$CARGO_HOME_DIR/bin/cargo$_exe" install cbindgen \
        --version "$CBINDGEN_VERSION" --locked --root "$(ccm_native_path "$_root")" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "building cbindgen $CBINDGEN_VERSION failed"
}

# Installs Corrosion from its source with the pinned CMake. Sets CORROSION_PREFIX.
rust_install_corrosion() {
    ccm_install_tool rust corrosion
    CORROSION_VERSION=$CCM_ROW_VERSION
    _src=$CCM_TOOL_BIN_DIR
    CORROSION_PREFIX="$CCM_HOME/toolchains/corrosion/$CORROSION_VERSION/install"
    if [ "$CCM_FORCE" != 1 ] && [ -f "$CORROSION_PREFIX/.built" ]; then
        ccm_log "Corrosion $CORROSION_VERSION is already installed in $CORROSION_PREFIX"
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would install Corrosion $CORROSION_VERSION into $CORROSION_PREFIX"
        return 0
    fi
    _build="$CCM_HOME/toolchains/corrosion/$CORROSION_VERSION/.build-$$"
    _tmp="$CCM_HOME/toolchains/corrosion/$CORROSION_VERSION/.install-$$"
    _log="$CCM_HOME/toolchains/corrosion/$CORROSION_VERSION/build.log"
    rm -rf "$_build" "$_tmp"
    ccm_log "installing Corrosion $CORROSION_VERSION (log: $_log)"
    # Corrosion's configure looks for rustc on PATH.
    if ! (
        PATH="$CARGO_HOME_DIR/bin:$PATH"
        RUSTUP_HOME="$(ccm_native_path "$RUSTUP_HOME_DIR")"
        CARGO_HOME="$(ccm_native_path "$CARGO_HOME_DIR")"
        RUSTUP_TOOLCHAIN=$RUST_VERSION
        export PATH RUSTUP_HOME CARGO_HOME RUSTUP_TOOLCHAIN
        "$CMAKE" -S "$(ccm_native_path "$_src")" -B "$(ccm_native_path "$_build")" -G Ninja \
            "-DCMAKE_MAKE_PROGRAM=$(ccm_native_path "$NINJA")" \
            "-DCMAKE_C_COMPILER=$(ccm_native_path "$CC_PATH")" \
            "-DCMAKE_CXX_COMPILER=$(ccm_native_path "$CXX_PATH")" -DCORROSION_BUILD_TESTS=OFF \
            "-DCMAKE_INSTALL_PREFIX=$(ccm_native_path "$_tmp")" &&
            "$CMAKE" --build "$(ccm_native_path "$_build")" &&
            "$CMAKE" --install "$(ccm_native_path "$_build")"
    ) >"$_log" 2>&1; then
        rm -rf "$_build" "$_tmp"
        tail -n 20 "$_log" >&2
        ccm_die "$CCM_EXIT_DOWNLOAD" "installing Corrosion failed; the full log is $_log"
    fi
    printf '%s\n' "$CCM_ROW_SHA256" >"$_tmp/.built"
    rm -rf "$CORROSION_PREFIX" "$_build"
    mv "$_tmp" "$CORROSION_PREFIX" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not move Corrosion into $CORROSION_PREFIX"
    ccm_log "installed Corrosion $CORROSION_VERSION into $CORROSION_PREFIX"
}

# rust.env: everything in cpp.env plus the Rust tools, since every run that builds Rust here
# is a mixed CMake + Cargo build.
rust_write_env() {
    _env="$CCM_HOME/toolchains/rust.env"
    _activate="$CCM_HOME/toolchains/activate-rust.sh"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would write $_env and $_activate"
        return 0
    fi
    _sep=:
    case $CCM_PLATFORM in windows-*) _sep=';' ;; esac
    _rust_prepend="$(ccm_env_path "$CARGO_HOME_DIR/bin")$_sep$(ccm_env_path "$NEXTEST_BIN")"
    _rust_prepend="$_rust_prepend$_sep$(ccm_env_path "$CBINDGEN_BIN")"
    _cpp_prepend="$(ccm_env_path "$LLVM_BIN")$_sep$(ccm_env_path "$CMAKE_BIN")$_sep$(ccm_env_path "$NINJA_BIN")"
    [ -z "$EXTRA_BIN" ] || _cpp_prepend="$_cpp_prepend$_sep$(ccm_env_path "$EXTRA_BIN")"
    _prefixes="$(ccm_env_path "$GTEST_PREFIX")$_sep$(ccm_env_path "$CORROSION_PREFIX")"
    _sh_path="$CARGO_HOME_DIR/bin:$NEXTEST_BIN:$CBINDGEN_BIN:$LLVM_BIN:$CMAKE_BIN:$NINJA_BIN"
    [ -z "$EXTRA_BIN" ] || _sh_path="$_sh_path:$EXTRA_BIN"
    if ! {
        printf '# CodeCoreMorph Rust toolchain (with the C++ one), written by install-toolchain.\n'
        printf '# Do not edit. rust %s (rustup %s), cargo-nextest %s, cbindgen %s,\n' \
            "$RUST_VERSION" "$RUSTUP_VERSION" "$NEXTEST_VERSION" "$CBINDGEN_VERSION"
        printf '# corrosion %s, %s, cmake %s, ninja %s, googletest %s and %s\n' \
            "$CORROSION_VERSION" "$LLVM_ID" "$CMAKE_VERSION" "$NINJA_VERSION" "$GTEST_VERSION" \
            "$GTEST98_VERSION"
        printf 'PATH_PREPEND=%s%s%s\n' "$_rust_prepend" "$_sep" "$_cpp_prepend"
        printf 'CC=%s\n' "$(ccm_env_path "$CC_PATH")"
        printf 'CXX=%s\n' "$(ccm_env_path "$CXX_PATH")"
        printf 'CMAKE_GENERATOR=Ninja\n'
        printf 'CMAKE_PREFIX_PATH=%s\n' "$_prefixes"
        printf 'CODECOREMORPH_CXX98_PREFIX_PATH=%s\n' "$(ccm_env_path "$GTEST98_PREFIX")"
        printf 'RUSTUP_HOME=%s\n' "$(ccm_env_path "$RUSTUP_HOME_DIR")"
        printf 'CARGO_HOME=%s\n' "$(ccm_env_path "$CARGO_HOME_DIR")"
        printf 'RUSTUP_TOOLCHAIN=%s\n' "$RUST_VERSION"
        # Builds never reach the network; crates come from the fetch step (SPEC 11.5).
        printf 'CARGO_NET_OFFLINE=true\n'
    } >"$_env.tmp" || ! mv -f "$_env.tmp" "$_env"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_env"
    fi
    if ! {
        printf '# Use the CodeCoreMorph Rust and C++ toolchain in this shell:  . "%s"\n' \
            "$_activate"
        # shellcheck disable=SC2016 # $PATH expands when the activation script runs
        printf 'PATH="%s:$PATH"\n' "$_sh_path"
        printf 'CC="%s"\n' "$(ccm_native_path "$CC_PATH")"
        printf 'CXX="%s"\n' "$(ccm_native_path "$CXX_PATH")"
        printf 'CMAKE_GENERATOR=Ninja\n'
        printf 'CMAKE_PREFIX_PATH="%s"\n' "$_prefixes"
        printf 'RUSTUP_HOME="%s"\n' "$(ccm_native_path "$RUSTUP_HOME_DIR")"
        printf 'CARGO_HOME="%s"\n' "$(ccm_native_path "$CARGO_HOME_DIR")"
        printf 'RUSTUP_TOOLCHAIN=%s\n' "$RUST_VERSION"
        printf 'export PATH CC CXX CMAKE_GENERATOR CMAKE_PREFIX_PATH RUSTUP_HOME CARGO_HOME'
        printf ' RUSTUP_TOOLCHAIN\n'
    } >"$_activate.tmp" || ! mv -f "$_activate.tmp" "$_activate"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_activate"
    fi
    ccm_log "wrote $_env"
    ccm_log "to use the toolchain in your own shell, run: . \"$_activate\""
}

install_rust() {
    [ -z "$CCM_OFFLINE" ] || ccm_die "$CCM_EXIT_USAGE" "--offline doesn't apply to rust: rustup" \
        "downloads the Rust release and cargo builds cbindgen themselves"
    install_cpp
    rust_install_toolchain
    ccm_install_tool rust cargo-nextest
    NEXTEST_VERSION=$CCM_ROW_VERSION
    NEXTEST_BIN=$CCM_TOOL_BIN_DIR
    rust_install_cbindgen
    rust_install_corrosion
    rust_write_env
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
# shellcheck disable=SC2086 # split the positional arguments into words
set -- $CCM_POSITIONAL
[ $# -eq 1 ] || ccm_die "$CCM_EXIT_USAGE" "expected one language: cpp, rust or all (see --help)"
language=$1
case $language in
    cpp | rust | all) ;;
    java | csharp)
        ccm_die "$CCM_EXIT_USAGE" "the $language toolchain is not available yet; available:" \
            "cpp, rust"
        ;;
    *) ccm_die "$CCM_EXIT_USAGE" "unknown language '$language'; available: cpp, rust, all" ;;
esac
ccm_setup
case $language in
    cpp) install_cpp ;;
    *) install_rust ;; # rust and all: the Rust toolchain includes the C++ one
esac
ccm_log "done"
