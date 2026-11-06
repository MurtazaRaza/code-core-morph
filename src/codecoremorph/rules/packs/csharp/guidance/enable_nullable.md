# Enable nullable reference types

The sites are files without a nullable context, and the nullable warnings (CS8600 to CS8799)
the last build gave in them. For each file:

1. Put `#nullable enable` on the file's first line (before the `using`s).
2. Annotate what can be null today, as the code behaves now: a field set later than the
   constructor, a parameter callers pass `null` to, a method that returns `null`, an `as` cast
   are `string?`, `Item?`. What is never null stays as it is.
3. Fix each warning the annotations leave without changing behavior: check for null where the
   code already handled it (`if (x == null) return;` becomes the flow the compiler follows),
   and use `!` only where the code guarantees the value but the compiler can't see it (say
   why in a comment).

Rules:

- Change no behavior. Don't add `ArgumentNullException` checks the code didn't have, and don't
  turn a `NullReferenceException` into another exception.
- Signatures change only by `?`: no renamed members, no new overloads. The annotations are not
  part of the API this rule checks.
- Don't use `#nullable disable`, `#pragma warning disable` or `<NoWarn>` to silence a warning.
- A warning about a type in another file goes away when that file's unit annotates it; don't
  edit files outside your unit.

The build after your edits shows what is left: each warning comes back to you with its line.
