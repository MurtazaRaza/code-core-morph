The build failed with 3 errors and 1 warning.

### In the unit's files

src/a.cpp:4:5: error: no matching function for call to 'make'
  note: src/a.cpp:9:5: candidate function not viable
```
  1 | line 1
  2 | line 2
  3 | line 3
> 4 | line 4
  5 | line 5
  6 | line 6
  7 | line 7
```

linker: error: undefined reference to `geo::area()' [undefined-reference]

src/a.cpp:3:5: warning: unused variable 'x' [-Wunused-variable]
```
  1 | line 1
  2 | line 2
> 3 | line 3
  4 | line 4
  5 | line 5
  6 | line 6
```

### In other files, likely caused by your changes to src/a.cpp

src/b.cpp:1:5: error: use of undeclared identifier 'a'
```
> 1 | int b() { return a(); }
```