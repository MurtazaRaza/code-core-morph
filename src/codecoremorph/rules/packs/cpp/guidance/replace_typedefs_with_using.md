# Replace typedef with using

clang-tidy (`modernize-use-using`) has already converted the simple cases. Convert what is
left, keeping every name and type exactly the same:

- `typedef T Name;` → `using Name = T;`
- Function pointers: `typedef int (*Fn)(int, char);` → `using Fn = int (*)(int, char);`
- Arrays: `typedef char Buffer[256];` → `using Buffer = char[256];`
- `typedef struct Tag { ... } Name;` in C++-only code → `struct Name { ... };` only if nothing
  uses `Tag`; otherwise `struct Tag { ... }; using Name = Tag;`
- Typedefs inside macros: rewrite the macro body to use `using`, unless the macro is also used
  from C.
- Dependent types in templates: `typedef typename T::value_type V;` →
  `using V = typename T::value_type;`

Never convert typedefs in headers that C code includes: `using` is not C.
