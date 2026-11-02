# Convert owning raw pointers to smart pointers

Decide ownership first, then change types. A pointer is **owning** when the code that holds it
is responsible for deleting it: a member deleted in the destructor, a local deleted before
return, a function result the caller must delete.

- Single owner → `std::unique_ptr<T>`; create with `std::make_unique<T>(...)`.
- Arrays allocated with `new T[n]` → `std::vector<T>`, or `std::unique_ptr<T[]>` when the
  buffer is passed to C APIs.
- Shared ownership (several owners, reference counting by hand) → `std::shared_ptr<T>`.
- Remove the matching `delete` / `delete[]`, and destructors, copy constructors and copy
  assignment operators that existed only to manage the pointer. A class holding a
  `unique_ptr` becomes move-only: if the old class was copyable and callers copy it, give it a
  copy constructor that deep-copies, so behavior stays the same.
- Factory functions that returned `T*` for the caller to delete return `std::unique_ptr<T>`;
  update every caller in the unit.
- Containers of owning pointers (`std::vector<T*>` cleaned up in a loop) →
  `std::vector<std::unique_ptr<T>>`.

Keep **non-owning** pointers raw (observers, back-pointers, optional references, pointers into
buffers). List each one you kept raw, and why, in your notes. Never change the declarations in
headers shared with C.
