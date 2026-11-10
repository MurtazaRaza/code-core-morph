# Port a C# program to C++

The unit moves a program of the solution (a console project: `OutputType` Exe) to C++, whole:
its `Main` and the program project's other types. Its code becomes a function of the C++
library, and the C# program becomes a façade whose `Main` calls it through the bridge, with the
program's arguments, and exits with its code. So the tests that run the program run the C++. A
CMake program of the same name calls the same function; it is the program once the C# is
retired.

The library types the program uses are C++ already, behind their façades: the program's C++
uses their C++ classes directly.

## What the codemod made

- `cpp/src/<program>_main.hpp` and `.cpp`, in the library's sources. The header declares
  `int <program>_main(const std::vector<std::string>& args)`: the arguments without the
  program's name, as C#'s `Main(string[] args)` gets them, and the exit code returned. **The
  program goes in the `.cpp`**: `<program>_main` at global scope, as declared, and the program's
  other code in a namespace from its C# one (`Zoo.Cli` is `zoo::cli`), as the type units do.
- `cpp/bridge/<program>.h` and `.cpp`: the export the C# `Main` calls,
  `<name>_<program>_main(const char **args_data, int32_t args_count, int32_t *result)`, written
  whole: it calls `<program>_main` and flushes `std::cout` and `std::cerr`, since the .NET host
  exits without flushing C++'s streams. Don't edit it.
- `cpp/programs/<program>.cpp`: the CMake program's `main`, which calls `<program>_main`. Don't
  edit it.
- Beside the program's project file: `CodeCoreMorph.Interop.cs` and the generated
  `CodeCoreMorph.Native.g.cs`, as for a type unit, so the program's façade can call the export.

## What the unit does

1. Write the program in C++. Keep its behavior byte for byte: the same text on stdout and
   stderr, in the same order, read from stdin as the C# read it, and the same exit code.
   `Console.WriteLine` is `std::cout << ... << '\n'`.
2. Turn the C# program into the façade. `Main` keeps its signature and calls the export:

   ```csharp
   CcmInterop.Check(CcmNative.<name>_<program>_main(args, args.Length, out var code));
   return code;  // from an int Main; a void Main sets Environment.ExitCode = code
   ```

   An exception the C++ throws reaches `Main` as the .NET exception, and leaves it as the C#
   program's did. Delete the program's other C# files: their code is C++ now, and nothing
   outside the program uses it (the plan refuses a program whose types other C# uses).
3. Map the program's members in `cpp/symbol-map.toml`, as a type unit does.

Formatting follows the C#: numbers as the invariant culture prints them, `decimal` through
`ccm::Decimal::to_string`, and `Console.Write`'s newline as `\n`.
