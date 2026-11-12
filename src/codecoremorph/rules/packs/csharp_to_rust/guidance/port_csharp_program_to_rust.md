# Port a C# program to Rust

The unit moves a program of the solution (a console project: `OutputType` Exe) to Rust, whole:
its `Main` and the program project's other types. Its code becomes a function of the Rust
library, and the C# program becomes a façade whose `Main` calls it through the bridge, with the
program's arguments, and exits with its code. So the tests that run the program run the Rust. A
Cargo program calls the same function; it is the program once the C# is retired.

The library types the program uses are Rust already, behind their façades: the program's Rust
uses their Rust types directly.

## What the codemod made

- `rust/<name>_rs/src/<program>_main.rs`, a module of the library: `pub fn <program>_main(args:
  &[String]) -> i32`, the arguments without the program's name, as C#'s `Main(string[] args)`
  gets them, and the exit code returned. **The program goes here**, replacing the `todo!()`:
  the function as declared, and the program's other code beside it (or in modules from its C#
  namespaces), as the type units do.
- `rust/<name>_rs/src/bridge/<program>.rs`: the export the C# `Main` calls,
  `<name>_<program>_main(args_data, args_count, result)`, written whole: it calls
  `<program>_main` and flushes stdout and stderr, since the .NET host exits without flushing
  Rust's buffers. Don't edit it.
- `rust/<name>_rs/src/bin/<program>.rs`: the Cargo program's `main`, which calls
  `<program>_main` and exits with its code. Don't edit it.
- Beside the program's project file: `CodeCoreMorph.Interop.cs` and the generated
  `CodeCoreMorph.Native.g.cs`, as for a type unit, so the program's façade can call the export.

## What the unit does

1. Write the program in Rust. Keep its behavior byte for byte: the same text on stdout and
   stderr, in the same order, read from stdin as the C# read it, and the same exit code.
   `Console.WriteLine` is `println!` (`\n`, as C# writes on Linux); an error the C# let escape
   `Main` is a message on stderr and its exit code, as the .NET host gave them.
2. Turn the C# program into the façade. `Main` keeps its signature and calls the export:

   ```csharp
   CcmInterop.Check(CcmNative.<name>_<program>_main(args, args.Length, out var code));
   return code;  // from an int Main; a void Main sets Environment.ExitCode = code
   ```

   An error the Rust returns reaches `Main` as the .NET exception, and leaves it as the C#
   program's did. Delete the program's other C# files: their code is Rust now, and nothing
   outside the program uses it (the plan refuses a program whose types other C# uses).
3. Map the program's members in `rust/symbol-map.toml`, as a type unit does.

Formatting follows the C#: numbers as the invariant culture prints them, `decimal` through
`Decimal`'s `Display`, and `Console.Write`'s newline as `\n`.
