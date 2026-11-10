# Retire the C#

The last unit. Every type is C++ behind its façade, every test is a GoogleTest test, and the
programs are C++: the codemod deleted the C# projects and their files, the solution, the MSBuild
files only the C# needed (`Directory.Build.props`, `global.json`, `nuget.config`), the generated
declarations and the façades' support, and the bridge (`cpp/bridge`, the `<name>_native`
library). What is left is a CMake project: the top-level `CMakeLists.txt`, the C++ library under
`cpp/`, its tests under `cpp/tests` and its programs.

With `migration.target.keep_source_api`, the C# public API stays as a .NET library over the
native one, for .NET consumers: the codemod deleted only the programs, which are CMake programs
now, and the test projects that reference them, and took them out of the solution. The library's
façade project, the bridge, the support files and the C# tests of the kept API stay, and both
builds run in the verification.

## What the unit does

1. Check that the project builds and its tests pass without the C#: the build and the tests of
   the verification show it. Fix what still refers to the C# (a comment that names a façade, a
   path in a CMake file, a C++ include of a bridge header).
2. The test map: every C# test of the baseline that no longer runs needs its entry, each mapped
   GoogleTest test run and passing. The earlier units mapped theirs; add what is missing.
3. Rewrite notes in `cpp/symbol-map.toml` that describe the façades, which are gone; the map
   stays as the record of where each C# member went.

`codecoremorph_decimal.hpp` and `codecoremorph_exceptions.hpp` stay: the C++ uses them. Keep
`CODECOREMORPH_SANITIZE` in the top-level `CMakeLists.txt`: it is how the tests run the C++
under the sanitizers, and it is off unless a build turns it on.
