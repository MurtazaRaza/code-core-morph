# Raise the C# version

The project files already name the target: a codemod raised `TargetFramework` (a `net` or
`netcoreapp` framework older than the target's .NET is now the target's) and `LangVersion`.
The build and the tests now run on the target's framework. Your job is to make the code
compile and behave the same there. Change behavior nowhere.

Only the sections between the source and the target apply.

## Any bump of the framework

- An API the new framework marks obsolete as an error (`error SYSLIB…`) must be replaced, not
  suppressed: no `#pragma warning disable`, no `<NoWarn>`, no
  `EnableUnsafeBinaryFormatterSerialization`.
- A package that doesn't support the new framework (`NU1202`): raise it to the lowest version
  that does, and say so in your notes.
- A `netstandard2.0` library keeps its framework; it now builds at the target's language
  version. A feature that needs the runtime's support (default interface members) or a type
  the framework lacks (`init` needs `IsExternalInit`, `required` needs
  `RequiredMemberAttribute`) is not this rule's: leave the code as it is.

## .NET 5 to 7

- Obsolete as warnings, so only where they break the build (`TreatWarningsAsErrors`):
  `WebRequest`, `HttpWebRequest` and `WebClient` (use `HttpClient`), `Thread.Abort`,
  `RNGCryptoServiceProvider` (use `RandomNumberGenerator`), the `*Managed` hash classes
  (`SHA256.Create()`), code access security.

## .NET 8

- `BinaryFormatter` is an error (SYSLIB0011), and it throws at run time. Replace what it does
  with what the code needs, and keep the result the same:
  - a deep copy: copy the object graph by hand (a copy constructor, or copying each field and
    each collection), not by serializing it;
  - data written to a file or sent somewhere: `System.Text.Json` (`JsonSerializer`) or
    `DataContractSerializer`, but only when nothing reads data the old code wrote. If
    something does, stop and say so in your notes.
- Formatter-based serialization (`ISerializable`'s `(SerializationInfo, StreamingContext)`
  constructors, `GetObjectData`, `[Serializable]` with `IFormatter`) is obsolete as warnings
  (SYSLIB0050, SYSLIB0051). Leave `[Serializable]` alone unless it breaks the build.

## .NET 9 and 10

- `BinaryFormatter` always throws: the same as for .NET 8.
- Loading a certificate through `X509Certificate2`'s constructors is obsolete
  (`X509CertificateLoader`), as a warning.

## The language

- C# 14 makes `field` a keyword inside property accessors: a member named `field` used there
  becomes `this.field` (or `@field`).
- A lambda has a natural type from C# 10, so an overloaded call that took a lambda can pick
  another overload: if a test now fails at such a call, cast the lambda to the delegate type
  the old overload took.

Edit only the files in your unit. When the build is green and the tests pass, you are done;
don't modernize code that compiles (later rules do that).
