3 test(s) that passed before the migration fail now. Fix the code, not the tests.

### gtest:Geo.Area: failed
Expected: 4
  Actual: 5
```
geo_test.cpp:12
```

### ctest:geo_test: timeout

### No longer run: gtest:Geo.Removed

---

1 typedefs site(s) remain in the unit's files:
- include/a.h:3: typedef int Id;