from __future__ import annotations

import pytest

from codecoremorph.core.globs import PathMatcher


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("third_party/**", "third_party/zlib/zlib.h", True),
        ("third_party/**", "src/third_party/x.h", False),
        ("third_party", "third_party/x/y.h", True),
        ("third_party/", "third_party/x.h", True),
        ("/build", "build/out.o", True),
        ("/build", "src/build/out.o", False),
        ("**/generated/**", "a/b/generated/c.cpp", True),
        ("**/generated/**", "generated/c.cpp", True),
        ("*.pb.cc", "proto/deep/msg.pb.cc", True),
        ("*.pb.cc", "proto/msg.pb.h", False),
        ("src/*.h", "src/a.h", True),
        ("src/*.h", "src/sub/a.h", False),
        ("src/**/*.h", "src/sub/deep/a.h", True),
        ("src/**/*.h", "src/a.h", True),
        ("file?.c", "dir/file1.c", True),
        ("file?.c", "dir/file10.c", False),
        ("[ab].c", "a.c", True),
        ("[!ab].c", "a.c", False),
        ("[!ab].c", "c.c", True),
        ("Vendor", "vendor/x.h", False),  # case-sensitive everywhere, like git
    ],
)
def test_patterns(pattern: str, path: str, expected: bool) -> None:
    assert PathMatcher([pattern]).matches(path) is expected


def test_first_match_and_empty() -> None:
    matcher = PathMatcher(["", "docs/**", "*.md"])
    assert matcher.first_match("docs/a.md") == "docs/**"
    assert matcher.first_match("README.md") == "*.md"
    assert matcher.first_match("src/a.cpp") is None
    assert not PathMatcher([])
    assert not PathMatcher(["   "]).matches("anything")
