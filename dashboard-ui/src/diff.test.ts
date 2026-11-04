import { describe, expect, it } from "vitest";

import { displayPath, GAP, languageOf, parseUnifiedDiff, sidesOf } from "./diff";

const TWO_HUNKS = [
  "diff --git a/src/geo.cpp b/src/geo.cpp",
  "index 1111111..2222222 100644",
  "--- a/src/geo.cpp",
  "+++ b/src/geo.cpp",
  "@@ -1,3 +1,3 @@ namespace geo {",
  " #include \"geo.h\"",
  "-Vector* make() { return new Vector; }",
  "+std::unique_ptr<Vector> make() { return std::make_unique<Vector>(); }",
  " int a;",
  "@@ -10,2 +10,3 @@",
  " int b;",
  "+int c;",
  " int d;",
  "\\ No newline at end of file",
  "diff --git a/docs/new.md b/docs/new.md",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/docs/new.md",
  "@@ -0,0 +1,2 @@",
  "+# New",
  "+text",
  "",
].join("\n");

describe("parseUnifiedDiff", () => {
  it("splits files and hunks and counts lines", () => {
    const [geo, doc] = parseUnifiedDiff(TWO_HUNKS);
    expect(geo?.oldPath).toBe("src/geo.cpp");
    expect(geo?.newPath).toBe("src/geo.cpp");
    expect(geo?.hunks.map((h) => [h.oldStart, h.newStart, h.lines.length])).toEqual([
      [1, 1, 4],
      [10, 10, 3],
    ]);
    expect(geo?.hunks[0]?.header).toBe("namespace geo {");
    expect([geo?.added, geo?.removed]).toEqual([2, 1]);
    expect(doc?.oldPath).toBeNull();
    expect(doc?.newPath).toBe("docs/new.md");
    expect(doc?.added).toBe(2);
  });

  it("reads a removed line that looks like a file header as part of its hunk", () => {
    const patch = [
      "--- a/x.sql",
      "+++ b/x.sql",
      "@@ -1,2 +1,1 @@",
      "--- a comment",
      " keep",
      "",
    ].join("\n");
    const [file] = parseUnifiedDiff(patch);
    expect(parseUnifiedDiff(patch)).toHaveLength(1);
    expect(file?.hunks[0]?.lines).toEqual([
      { kind: "-", text: "-- a comment" },
      { kind: " ", text: "keep" },
    ]);
  });

  it("accepts CRLF patches and single-line hunks", () => {
    const patch = "--- a/a.h\r\n+++ b/a.h\r\n@@ -3 +3 @@\r\n-old\r\n+new\r\n";
    const [file] = parseUnifiedDiff(patch);
    expect(file?.hunks[0]?.lines).toEqual([
      { kind: "-", text: "old" },
      { kind: "+", text: "new" },
    ]);
  });

  it("returns nothing for an empty patch", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });
});

describe("sidesOf", () => {
  it("keeps real line numbers and marks the gap between hunks", () => {
    const [geo] = parseUnifiedDiff(TWO_HUNKS);
    const sides = sidesOf(geo!);
    expect(sides.original.split("\n")).toEqual([
      '#include "geo.h"',
      "Vector* make() { return new Vector; }",
      "int a;",
      GAP,
      "int b;",
      "int d;",
    ]);
    expect(sides.originalNumbers).toEqual([1, 2, 3, null, 10, 11]);
    expect(sides.modifiedNumbers).toEqual([1, 2, 3, null, 10, 11, 12]);
  });
});

describe("paths and languages", () => {
  it("names renames and picks a language from the extension", () => {
    expect(displayPath({ oldPath: "a.h", newPath: "b.h", hunks: [], binary: false, added: 0, removed: 0 })).toBe(
      "a.h → b.h",
    );
    expect(languageOf("src/geo.cpp")).toBe("cpp");
    expect(languageOf("include/geo.h")).toBe("c");
    expect(languageOf("src/lib.rs")).toBe("rust");
    expect(languageOf("CMakeLists.txt")).toBe("plaintext");
  });
});
