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
        rustfmt), cargo-nextest, cbindgen, bindgen and Corrosion, for C++
        and C to Rust ports; rustup and cargo download Rust, cbindgen and
        bindgen themselves, so --offline doesn't apply
  java  a Temurin JDK for each Java standard (8, 11, 17, 21, 25), Maven, the
        Spoon jar, and a Maven repository with OpenRewrite and the plugins
        builds run offline; Maven downloads those itself, so --offline
        doesn't apply
  csharp
        the .NET SDK (with dotnet format and Roslyn) and the .NET 8 and 6
        runtimes beside its own, so tests run on their framework's runtime
  all   every available language (cpp, rust, java and csharp)

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

# The Rust release rustup installs, and the cbindgen (D-56) and bindgen (D-68) releases cargo
# builds. rustup and cargo verify what they download themselves (SPEC 7.15 rule 1).
RUST_VERSION=1.99.1
CBINDGEN_VERSION=0.29.4
BINDGEN_VERSION=0.73.2

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

# Builds the pinned bindgen (the bindgen-cli crate) with cargo, unless it is already there.
# Sets BINDGEN_BIN. It loads libclang when it runs (LIBCLANG_PATH in rust.env).
rust_install_bindgen() {
    _root="$CCM_HOME/toolchains/bindgen/$BINDGEN_VERSION"
    BINDGEN_BIN="$_root/bin"
    if [ "$CCM_FORCE" != 1 ] && [ -x "$BINDGEN_BIN/bindgen$_exe" ]; then
        ccm_log "bindgen $BINDGEN_VERSION is already installed in $_root"
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would build bindgen $BINDGEN_VERSION with cargo install --locked into $_root"
        return 0
    fi
    ccm_log "building bindgen $BINDGEN_VERSION (cargo install --locked)"
    PATH="$LLVM_BIN:$PATH" RUSTUP_HOME="$(ccm_native_path "$RUSTUP_HOME_DIR")" CARGO_HOME="$(ccm_native_path "$CARGO_HOME_DIR")" \
        RUSTUP_TOOLCHAIN="$RUST_VERSION" "$CARGO_HOME_DIR/bin/cargo$_exe" install bindgen-cli \
        --version "$BINDGEN_VERSION" --locked --root "$(ccm_native_path "$_root")" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "building bindgen $BINDGEN_VERSION failed"
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
    _rust_prepend="$_rust_prepend$_sep$(ccm_env_path "$CBINDGEN_BIN")$_sep$(ccm_env_path "$BINDGEN_BIN")"
    # bindgen loads libclang from LLVM's lib directory. llvm-mingw has no libclang.dll, so
    # bindgen runs in Docker on Windows for now (D-68).
    _libclang=""
    case $CCM_PLATFORM in windows-*) ;; *) _libclang="${LLVM_BIN%/bin}/lib" ;; esac
    _cpp_prepend="$(ccm_env_path "$LLVM_BIN")$_sep$(ccm_env_path "$CMAKE_BIN")$_sep$(ccm_env_path "$NINJA_BIN")"
    [ -z "$EXTRA_BIN" ] || _cpp_prepend="$_cpp_prepend$_sep$(ccm_env_path "$EXTRA_BIN")"
    _prefixes="$(ccm_env_path "$GTEST_PREFIX")$_sep$(ccm_env_path "$CORROSION_PREFIX")"
    _sh_path="$CARGO_HOME_DIR/bin:$NEXTEST_BIN:$CBINDGEN_BIN:$BINDGEN_BIN:$LLVM_BIN:$CMAKE_BIN:$NINJA_BIN"
    [ -z "$EXTRA_BIN" ] || _sh_path="$_sh_path:$EXTRA_BIN"
    if ! {
        printf '# CodeCoreMorph Rust toolchain (with the C++ one), written by install-toolchain.\n'
        printf '# Do not edit. rust %s (rustup %s), cargo-nextest %s, cbindgen %s, bindgen %s,\n' \
            "$RUST_VERSION" "$RUSTUP_VERSION" "$NEXTEST_VERSION" "$CBINDGEN_VERSION" \
            "$BINDGEN_VERSION"
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
        [ -z "$_libclang" ] || printf 'LIBCLANG_PATH=%s\n' "$(ccm_env_path "$_libclang")"
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
        if [ -n "$_libclang" ]; then
            printf 'LIBCLANG_PATH="%s"\n' "$(ccm_native_path "$_libclang")"
            printf 'export LIBCLANG_PATH\n'
        fi
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
        "downloads the Rust release and cargo builds cbindgen and bindgen themselves"
    install_cpp
    rust_install_toolchain
    ccm_install_tool rust cargo-nextest
    NEXTEST_VERSION=$CCM_ROW_VERSION
    NEXTEST_BIN=$CCM_TOOL_BIN_DIR
    rust_install_cbindgen
    rust_install_bindgen
    rust_install_corrosion
    rust_write_env
}

# The Maven artifacts the Java adapter runs offline (D-63): OpenRewrite's plugin and recipes, and
# the dependency plugin of its dependency step. Maven downloads them, checking each
# against the repository's checksums (-C), into toolchains/java/m2, which builds chain behind
# their own repository (maven.repo.local.tail). java.env names them for the adapter.
REWRITE_PLUGIN=org.openrewrite.maven:rewrite-maven-plugin:6.46.1
REWRITE_RECIPES=org.openrewrite.recipe:rewrite-migrate-java:3.42.1,org.openrewrite.recipe:rewrite-static-analysis:2.41.1
DEPENDENCY_PLUGIN=org.apache.maven.plugins:maven-dependency-plugin:3.11.0
JAVA_STANDARDS="8 11 17 21 25"

# Installs the JDK of each standard the manifest pins for this platform. Sets JDK_LINES (the
# CODECOREMORPH_JDK_N lines of java.env), JDK_IDS, JDK_NEWEST (the newest JDK's home) and
# JDK_NEWEST_BIN.
java_install_jdks() {
    JDK_LINES=""
    JDK_IDS=""
    for _n in $JAVA_STANDARDS; do
        if ! ccm_has_row java "jdk$_n"; then
            ccm_warn "Temurin publishes no JDK $_n for $CCM_PLATFORM; projects at Java $_n" \
                "build in Docker (sandbox.mode: docker)"
            continue
        fi
        ccm_install_tool java "jdk$_n"
        _home=${CCM_TOOL_BIN_DIR%/bin}
        JDK_LINES="$JDK_LINES
CODECOREMORPH_JDK_$_n=$(ccm_env_path "$_home")"
        JDK_IDS="$JDK_IDS jdk$_n $CCM_ROW_VERSION,"
        JDK_NEWEST=$_home
        JDK_NEWEST_BIN=$CCM_TOOL_BIN_DIR
    done
    JDK_IDS=${JDK_IDS%,}
}

# Writes CodeCoreMorph's Maven settings (builds use them instead of the user's
# ~/.m2/settings.xml, its mirrors and credentials) and fills the tools repository, unless it
# already holds the same artifacts. Sets MAVEN_SETTINGS and MAVEN_TOOLS_REPO.
java_fill_repository() {
    _java_root="$CCM_HOME/toolchains/java"
    MAVEN_SETTINGS="$_java_root/settings.xml"
    MAVEN_TOOLS_REPO="$_java_root/m2"
    _stamp="maven $MAVEN_VERSION $REWRITE_PLUGIN $REWRITE_RECIPES $DEPENDENCY_PLUGIN"
    if [ "$CCM_FORCE" != 1 ] && [ -f "$MAVEN_TOOLS_REPO/.built" ]; then
        _have=""
        read -r _have <"$MAVEN_TOOLS_REPO/.built" || true
        if [ "${_have%"$_ccm_cr"}" = "$_stamp" ]; then
            ccm_log "the Maven tools repository is already filled in $MAVEN_TOOLS_REPO"
            return 0
        fi
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would download OpenRewrite and the dependency step's plugins with Maven into" \
            "$MAVEN_TOOLS_REPO"
        return 0
    fi
    { mkdir -p "$_java_root" && : >"$_java_root/m2.log"; } 2>/dev/null ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not create $_java_root"
    if ! {
        printf '<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">\n'
        printf '  <!-- CodeCoreMorph builds use these settings instead of ~/.m2/settings.xml.\n'
        printf '       Written by install-toolchain. Do not edit. -->\n'
        printf '  <interactiveMode>false</interactiveMode>\n'
        printf '</settings>\n'
    } >"$MAVEN_SETTINGS.tmp" || ! mv -f "$MAVEN_SETTINGS.tmp" "$MAVEN_SETTINGS"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $MAVEN_SETTINGS"
    fi
    _tmp="$_java_root/.m2-$$"
    rm -rf "$_tmp"
    ccm_log "downloading OpenRewrite and the dependency step's plugins with Maven (log:" \
        "$_java_root/m2.log)"
    _mvn="$MAVEN_BIN/mvn"
    case $CCM_PLATFORM in windows-*) _mvn="$MAVEN_BIN/mvn.cmd" ;; esac
    # shellcheck disable=SC2046 # one artifact per word
    for _artifact in "$REWRITE_PLUGIN" $(printf '%s\n' "$REWRITE_RECIPES" | tr ',' ' ') \
        "$DEPENDENCY_PLUGIN"; do
        # Outside any project, so no POM's build extensions load.
        if ! (cd "$_java_root" && JAVA_HOME="$(ccm_native_path "$JDK_NEWEST")" "$_mvn" -B -ntp -C \
            -s "$(ccm_native_path "$MAVEN_SETTINGS")" \
            "-Dmaven.repo.local=$(ccm_native_path "$_tmp")" "$DEPENDENCY_PLUGIN:get" \
            "-Dartifact=$_artifact") >>"$_java_root/m2.log" 2>&1; then
            rm -rf "$_tmp"
            tail -n 20 "$_java_root/m2.log" >&2
            ccm_die "$CCM_EXIT_DOWNLOAD" "Maven could not download $_artifact; the full log is" \
                "$_java_root/m2.log"
        fi
    done
    printf '%s\n' "$_stamp" >"$_tmp/.built"
    rm -rf "$MAVEN_TOOLS_REPO"
    mv "$_tmp" "$MAVEN_TOOLS_REPO" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not move the Maven repository into $MAVEN_TOOLS_REPO"
    ccm_log "filled the Maven tools repository in $MAVEN_TOOLS_REPO"
}

java_write_env() {
    _env="$CCM_HOME/toolchains/java.env"
    _activate="$CCM_HOME/toolchains/activate-java.sh"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would write $_env and $_activate"
        return 0
    fi
    _sep=:
    case $CCM_PLATFORM in windows-*) _sep=';' ;; esac
    _prepend="$(ccm_env_path "$MAVEN_BIN")$_sep$(ccm_env_path "$JDK_NEWEST_BIN")"
    if ! {
        printf '# CodeCoreMorph Java toolchain, written by install-toolchain. Do not edit.\n'
        printf '#%s, maven %s, spoon %s\n' "$JDK_IDS" "$MAVEN_VERSION" "$SPOON_VERSION"
        printf 'PATH_PREPEND=%s\n' "$_prepend"
        # The newest JDK; the Java adapter sets JAVA_HOME to the build standard's own.
        printf 'JAVA_HOME=%s\n' "$(ccm_env_path "$JDK_NEWEST")"
        printf '%s\n' "${JDK_LINES#?}"
        # The adapter starts Maven's launcher on the build standard's JDK itself, not mvn.
        printf 'CODECOREMORPH_MAVEN_HOME=%s\n' "$(ccm_env_path "${MAVEN_BIN%/bin}")"
        printf 'CODECOREMORPH_MAVEN_LAUNCHER=%s\n' "$(ccm_env_path "$MAVEN_LAUNCHER")"
        printf 'CODECOREMORPH_MAVEN_SETTINGS=%s\n' "$(ccm_env_path "$MAVEN_SETTINGS")"
        printf 'CODECOREMORPH_MAVEN_TOOLS_REPO=%s\n' "$(ccm_env_path "$MAVEN_TOOLS_REPO")"
        printf 'CODECOREMORPH_MAVEN_DEPENDENCY_PLUGIN=%s\n' "$DEPENDENCY_PLUGIN"
        printf 'CODECOREMORPH_REWRITE_PLUGIN=%s\n' "$REWRITE_PLUGIN"
        printf 'CODECOREMORPH_REWRITE_RECIPES=%s\n' "$REWRITE_RECIPES"
        printf 'CODECOREMORPH_SPOON_JAR=%s\n' "$(ccm_env_path "$SPOON_JAR")"
    } >"$_env.tmp" || ! mv -f "$_env.tmp" "$_env"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_env"
    fi
    if ! {
        printf '# Use the CodeCoreMorph Java toolchain in this shell:  . "%s"\n' "$_activate"
        # shellcheck disable=SC2016 # $PATH expands when the activation script runs
        printf 'PATH="%s:%s:$PATH"\n' "$MAVEN_BIN" "$JDK_NEWEST_BIN"
        printf 'JAVA_HOME="%s"\n' "$(ccm_native_path "$JDK_NEWEST")"
        printf 'export PATH JAVA_HOME\n'
    } >"$_activate.tmp" || ! mv -f "$_activate.tmp" "$_activate"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_activate"
    fi
    case $CCM_PLATFORM in
        windows-*)
            # Keep the cmd.exe activation script in step (install-toolchain.bat writes the same).
            _bat="$CCM_HOME/toolchains/activate-java.bat"
            if ! {
                printf '@echo off\r\n'
                printf 'rem Use the CodeCoreMorph Java toolchain in this cmd.exe window:  call "%s"\r\n' \
                    "$(ccm_env_path "$_bat")"
                printf 'set "PATH=%s;%%PATH%%"\r\n' "$_prepend"
                printf 'set "JAVA_HOME=%s"\r\n' "$(ccm_env_path "$JDK_NEWEST")"
            } >"$_bat.tmp" || ! mv -f "$_bat.tmp" "$_bat"; then
                ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_bat"
            fi
            ;;
    esac
    ccm_log "wrote $_env"
    ccm_log "to use the toolchain in your own shell, run: . \"$_activate\""
}

install_java() {
    [ -z "$CCM_OFFLINE" ] || ccm_die "$CCM_EXIT_USAGE" "--offline doesn't apply to java: Maven" \
        "downloads OpenRewrite and the plugins builds run offline itself"
    java_install_jdks
    ccm_install_tool java maven
    MAVEN_VERSION=$CCM_ROW_VERSION
    MAVEN_BIN=$CCM_TOOL_BIN_DIR
    MAVEN_LAUNCHER=""
    for _jar in "${MAVEN_BIN%/bin}"/boot/plexus-classworlds-*.jar; do
        MAVEN_LAUNCHER=$_jar
    done
    ccm_install_tool java spoon
    SPOON_VERSION=$CCM_ROW_VERSION
    SPOON_JAR="$CCM_TOOL_DIR/${CCM_ROW_URL##*/}"
    java_fill_repository
    java_write_env
}

# The .NET runtimes installed beside the SDK's own (D-65): a test project runs on its
# framework's runtime, and one without rolls forward to the next.
CSHARP_RUNTIMES="8 6"

# csharp_add_runtime DIR VERSION: copy the runtime installed in DIR into the SDK's dotnet root
# (SDK_DIR), where the dotnet host looks for it, unless it is there already.
csharp_add_runtime() {
    _src="$1/shared/Microsoft.NETCore.App/$2"
    _dst="$SDK_DIR/shared/Microsoft.NETCore.App/$2"
    if [ "$CCM_FORCE" != 1 ] && [ -f "$_dst/.installed" ]; then
        return 0
    fi
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would add the .NET $2 runtime to $SDK_DIR"
        return 0
    fi
    [ -d "$_src" ] || ccm_die "$CCM_EXIT_DOWNLOAD" "the .NET $2 runtime archive has no $_src"
    rm -rf "$_dst.tmp"
    if ! cp -R "$_src" "$_dst.tmp" || ! printf '%s\n' "$2" >"$_dst.tmp/.installed"; then
        rm -rf "$_dst.tmp"
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not copy the .NET $2 runtime into $SDK_DIR"
    fi
    rm -rf "$_dst"
    mv "$_dst.tmp" "$_dst" ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not copy the .NET $2 runtime into $SDK_DIR"
    ccm_log "added the .NET $2 runtime to $SDK_DIR"
}

# Writes NuGet's config for restores (nuget.org alone; the user's NuGet config is never read).
# Sets NUGET_CONFIG.
csharp_write_nuget_config() {
    NUGET_CONFIG="$CCM_HOME/toolchains/csharp/nuget.config"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would write $NUGET_CONFIG"
        return 0
    fi
    mkdir -p "${NUGET_CONFIG%/*}" 2>/dev/null ||
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not create ${NUGET_CONFIG%/*}"
    if ! {
        printf '<?xml version="1.0" encoding="utf-8"?>\n'
        printf '<!-- CodeCoreMorph restores use this config instead of the user'"'"'s NuGet config.\n'
        printf '     Written by install-toolchain. Do not edit. -->\n'
        printf '<configuration>\n'
        printf '  <packageSources>\n'
        printf '    <clear />\n'
        printf '    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" />\n'
        printf '  </packageSources>\n'
        printf '</configuration>\n'
    } >"$NUGET_CONFIG.tmp" || ! mv -f "$NUGET_CONFIG.tmp" "$NUGET_CONFIG"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $NUGET_CONFIG"
    fi
}

csharp_write_env() {
    _env="$CCM_HOME/toolchains/csharp.env"
    _activate="$CCM_HOME/toolchains/activate-csharp.sh"
    if [ "$CCM_DRY_RUN" = 1 ]; then
        ccm_log "would write $_env and $_activate"
        return 0
    fi
    _dotnet="$SDK_DIR/dotnet"
    case $CCM_PLATFORM in windows-*) _dotnet="$SDK_DIR/dotnet.exe" ;; esac
    _ref=""
    for _dir in "$SDK_DIR"/packs/Microsoft.NETCore.App.Ref/*/ref/net*; do
        [ -d "$_dir" ] && _ref=$_dir
    done
    [ -n "$_ref" ] || ccm_die "$CCM_EXIT_DOWNLOAD" "the .NET SDK in $SDK_DIR has no reference pack"
    if ! {
        printf '# CodeCoreMorph C# toolchain, written by install-toolchain. Do not edit.\n'
        printf '# dotnet-sdk %s,%s\n' "$SDK_VERSION" "$RUNTIME_IDS"
        printf 'PATH_PREPEND=%s\n' "$(ccm_env_path "$SDK_DIR")"
        printf 'DOTNET_ROOT=%s\n' "$(ccm_env_path "$SDK_DIR")"
        printf 'DOTNET_MULTILEVEL_LOOKUP=0\n'
        printf 'DOTNET_CLI_TELEMETRY_OPTOUT=1\n'
        printf 'DOTNET_NOLOGO=1\n'
        printf 'DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1\n'
        printf 'DOTNET_GENERATE_ASPNET_CERTIFICATE=false\n'
        printf 'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false\n'
        printf 'DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1\n'
        # The adapter compiles its index helper with the SDK's csc and Roslyn, against this pack.
        printf 'CODECOREMORPH_DOTNET=%s\n' "$(ccm_env_path "$_dotnet")"
        printf 'CODECOREMORPH_DOTNET_SDK=%s\n' "$(ccm_env_path "$SDK_DIR/sdk/$SDK_VERSION")"
        printf 'CODECOREMORPH_DOTNET_REF_PACK=%s\n' "$(ccm_env_path "$_ref")"
        printf 'CODECOREMORPH_NUGET_CONFIG=%s\n' "$(ccm_env_path "$NUGET_CONFIG")"
    } >"$_env.tmp" || ! mv -f "$_env.tmp" "$_env"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_env"
    fi
    if ! {
        printf '# Use the CodeCoreMorph C# toolchain in this shell:  . "%s"\n' "$_activate"
        # shellcheck disable=SC2016 # $PATH expands when the activation script runs
        printf 'PATH="%s:$PATH"\n' "$SDK_DIR"
        printf 'DOTNET_ROOT="%s"\n' "$(ccm_native_path "$SDK_DIR")"
        printf 'DOTNET_MULTILEVEL_LOOKUP=0\n'
        printf 'DOTNET_CLI_TELEMETRY_OPTOUT=1\n'
        printf 'export PATH DOTNET_ROOT DOTNET_MULTILEVEL_LOOKUP DOTNET_CLI_TELEMETRY_OPTOUT\n'
    } >"$_activate.tmp" || ! mv -f "$_activate.tmp" "$_activate"; then
        ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_activate"
    fi
    case $CCM_PLATFORM in
        windows-*)
            # Keep the cmd.exe activation script in step (install-toolchain.bat writes the same).
            _bat="$CCM_HOME/toolchains/activate-csharp.bat"
            if ! {
                printf '@echo off\r\n'
                printf 'rem Use the CodeCoreMorph C# toolchain in this cmd.exe window:  call "%s"\r\n' \
                    "$(ccm_env_path "$_bat")"
                printf 'set "PATH=%s;%%PATH%%"\r\n' "$(ccm_env_path "$SDK_DIR")"
                printf 'set "DOTNET_ROOT=%s"\r\n' "$(ccm_env_path "$SDK_DIR")"
                printf 'set "DOTNET_MULTILEVEL_LOOKUP=0"\r\n'
                printf 'set "DOTNET_CLI_TELEMETRY_OPTOUT=1"\r\n'
            } >"$_bat.tmp" || ! mv -f "$_bat.tmp" "$_bat"; then
                ccm_die "$CCM_EXIT_DOWNLOAD" "could not write $_bat"
            fi
            ;;
    esac
    ccm_log "wrote $_env"
    ccm_log "to use the toolchain in your own shell, run: . \"$_activate\""
}

install_csharp() {
    ccm_install_tool csharp dotnet-sdk
    SDK_VERSION=$CCM_ROW_VERSION
    SDK_DIR=$CCM_TOOL_DIR
    RUNTIME_IDS=""
    for _n in $CSHARP_RUNTIMES; do
        ccm_install_tool csharp "dotnet-runtime$_n"
        csharp_add_runtime "$CCM_TOOL_DIR" "$CCM_ROW_VERSION"
        RUNTIME_IDS="$RUNTIME_IDS dotnet-runtime$_n $CCM_ROW_VERSION,"
    done
    RUNTIME_IDS=${RUNTIME_IDS%,}
    csharp_write_nuget_config
    csharp_write_env
}

ccm_parse_args "$@"
if [ "$CCM_HELP" = 1 ]; then
    usage
    exit 0
fi
# shellcheck disable=SC2086 # split the positional arguments into words
set -- $CCM_POSITIONAL
[ $# -eq 1 ] || ccm_die "$CCM_EXIT_USAGE" "expected one language: cpp, rust, java, csharp or" \
    "all (see --help)"
language=$1
case $language in
    cpp | rust | java | csharp | all) ;;
    *)
        ccm_die "$CCM_EXIT_USAGE" "unknown language '$language'; available: cpp, rust, java," \
            "csharp, all"
        ;;
esac
ccm_setup
case $language in
    cpp) install_cpp ;;
    rust) install_rust ;; # the Rust toolchain includes the C++ one
    java) install_java ;;
    csharp) install_csharp ;;
    *)
        install_rust
        install_java
        install_csharp
        ;;
esac
ccm_log "done"
