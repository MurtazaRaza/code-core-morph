# Installing dependencies

Everything CodeCoreMorph downloads is pinned by version and SHA-256 in one manifest, and
checked before it is used. Nothing needs admin rights, nothing changes your system PATH, and
everything goes under one directory, `CODECOREMORPH_HOME`.

## Where things go

`CODECOREMORPH_HOME` defaults to:

| System | Default |
|---|---|
| Windows | `%LOCALAPPDATA%\codecoremorph` |
| macOS | `~/Library/Caches/codecoremorph` |
| Linux | `${XDG_CACHE_HOME:-~/.cache}/codecoremorph` |

Set the variable to put it elsewhere, for example on a bigger drive. It holds the verified
downloads (`downloads/`), the native toolchains (`toolchains/`), and the run worktrees and
build directories.

## What you install yourself

CodeCoreMorph detects these and tells you how to install them, but never installs them:

- **git** 2.30 or newer.
- **Docker**, for the default sandbox (Docker Desktop on Windows and macOS).
- For native C++ builds on Linux: the C and C++ standard library headers (`build-essential` or
  your distribution's equivalent) and `xz`. On macOS: the Xcode Command Line Tools.

## Native toolchains

```sh
codecoremorph toolchain install cpp      # or: scripts/install-toolchain.sh cpp (.bat on Windows)
codecoremorph toolchain list             # what is pinned, and what is installed
```

For C++ this installs LLVM 18 (clang, clang-tidy, clang-format; llvm-mingw on Windows, so no
Visual Studio is needed), CMake, Ninja, and GoogleTest built with that compiler: 1.12.1, plus
1.8.1 for projects that build as C++98/03. Your shell is unchanged; CodeCoreMorph finds the
tools through `toolchains/cpp.env`, and `toolchains/activate-cpp.sh` (or `.bat`) puts them on
the PATH of your own shell if you want them there.

`codecoremorph toolchain install rust`, for [ports to Rust](porting-to-rust.md), installs the
C++ toolchain and then Rust through rustup (with clippy and rustfmt; on Windows the
`x86_64-pc-windows-gnullvm` target, which links with llvm-mingw), cargo-nextest, cbindgen (built
from crates.io with `--locked`) and Corrosion. It writes `toolchains/rust.env`. rustup and Cargo
live in `toolchains/rust/`, apart from any Rust you have installed yourself.

Re-running is cheap: a tool whose pinned version is installed is skipped, unless you pass
`--force`. `--dry-run` shows what would be downloaded.

## Toolchain images

In Docker mode, a run pulls the pinned image the first time it needs it, and checks its digest.
`scripts/pull-images` pulls ahead of time. Until the images are published, build them from a
source checkout with `scripts/build-image cpp`; runs use the local build
(`codecoremorph-cpp:local`) until a published image is pinned.

## Local models

For Ollama models, install Ollama yourself, then:

```sh
scripts/pull-models.sh qwen3:4b     # .bat on Windows
```

`codecoremorph doctor -c migration_config.yaml` prints the command for the models your manifest
uses.

## Machines without internet access

On a machine with internet access, download the archives for the other machine's platform:

```sh
codecoremorph toolchain download cpp ./offline --platform windows-x64
```

(`--platform` is one of `windows-x64`, `linux-x64`, `linux-arm64`, `macos-arm64`, `macos-x64`.)
Every archive is checked against its pinned SHA-256 and saved under its cache name. Copy the
directory over, then install from it:

```sh
codecoremorph toolchain install cpp --offline ./offline
```

The install uses no network. It also accepts the files under their download names, so you can
fetch the manifest's URLs by hand, or copy the `downloads/` directory of a `CODECOREMORPH_HOME`
on the same platform. A missing or altered file stops the install and says which one to put
there. Use the same CodeCoreMorph version on both machines, so that both read the same
manifest.

Docker images and Ollama models have no offline install in this version: on a machine without
internet access, use native mode.

## Contributors

A source checkout sets itself up with `scripts/bootstrap-dev.sh` (`.bat` on Windows): uv,
Python 3.12, the dependencies, the pre-commit hooks, and Node.js for the dashboard's web UI.
It takes the same `--offline DIR`, and then lets uv use only its cache.
