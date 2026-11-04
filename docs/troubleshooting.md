# Troubleshooting

Start with `codecoremorph doctor` (and `codecoremorph doctor migration_config.yaml` for a given
manifest): it checks git, Docker, the toolchains and the models, and names the fix for each
problem. Every error message says what happened, why, and what to do next.

## Docker on Windows and macOS

**Docker isn't reachable.** Start Docker Desktop and wait until it says it is running. `doctor`
reports whether the daemon answers. With `sandbox.mode: auto`, CodeCoreMorph falls back to
native mode with a warning; `sandbox.mode: docker` makes Docker required.

**Docker Desktop on Windows uses WSL 2.** Docker Desktop runs containers in a WSL 2 virtual
machine. The project is mounted into the container from your Windows drive, which works, but
file access across that boundary is slow. For large projects, keep the repository inside the
WSL file system (`\\wsl$\...`) and run CodeCoreMorph from WSL, or use native mode.

**The disk fills up.** Docker Desktop keeps images in a virtual disk on the system drive by
default. Move it under Docker Desktop's settings (Resources, Disk image location). CodeCoreMorph's
own downloads and run directories go under `CODECOREMORPH_HOME`, which you can move with the
environment variable.

## The baseline doesn't build or test

`plan` and `run` start by building and testing your base commit. If that fails, they stop and
show the end of the build log and where the full log is.

- **CMake 4 refuses old projects.** CMake 4 doesn't configure files that ask for a version older
  than 3.5 (often in vendored dependencies). Add
  `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` to `build.cmake.configure_args`; `codecoremorph init`
  does when it sees such a file.
- **Tests are off by default.** If the project builds its tests only with an option, pass it in
  `build.cmake.configure_args`, for example `-DPROJECT_BUILD_TESTS=ON`.
- **No tests were found.** CodeCoreMorph runs tests through CTest, so the project needs
  `enable_testing()` (or `include(CTest)`) and `add_test`. A project with no tests at all can
  still be migrated with characterization tests, which `parity.characterization_tests`
  controls.
- **Some tests already fail.** Tests that fail or are flaky on the base commit are recorded and
  left out of the checks; the report names them.

## C++98 and C++03 projects

These build with GoogleTest 1.8.1, the last release that supports them, which the native
toolchain and the image both provide. If the build files set no standard, check
`migration.source.standard`: `init` guesses it from the code and says so.

## Windows paths

Git worktrees and build trees nest deeply. If git or the compiler fails with an error about a
path, set `CODECOREMORPH_HOME` to a short directory such as `D:\ccm`, which holds the run
worktrees.

## A run was interrupted

Ctrl-C stops a run cleanly at the next safe point, and a crash leaves its state on disk.
`codecoremorph resume` continues either; `codecoremorph status` shows where it is, and
`codecoremorph abort` ends it for good. A unit that was halfway through starts over.

## Another run holds the repository

One run at a time works in a repository. `codecoremorph status --all` lists the runs; resume or
abort the one in the way.
