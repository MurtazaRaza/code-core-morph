#include <gtest/gtest.h>

#include "mm/util/strings.h"

namespace {

using namespace mm::util;

TEST(StringsTest, LowerAndTrim) {
    EXPECT_EQ("hello world", toLower("Hello WORLD"));
    EXPECT_EQ("x y", trim("  x y \t\n"));
    EXPECT_EQ("", trim(" \n "));
}

TEST(StringsTest, SplitAndJoin) {
    StringList parts = split("a,b,,c", ',');
    ASSERT_EQ(4u, parts.size());
    EXPECT_EQ("", parts[2]);
    EXPECT_EQ("a-b--c", join(parts, "-"));
    EXPECT_EQ(1u, split("", ',').size());
    EXPECT_EQ("", join(StringList(), ","));
}

TEST(StringsTest, StartsWithAndReplace) {
    EXPECT_TRUE(startsWith("replace:a:b", "replace:"));
    EXPECT_FALSE(startsWith("re", "replace"));
    EXPECT_EQ("x-y-z", replaceAll("x.y.z", ".", "-"));
    EXPECT_EQ("aaa", replaceAll("aaa", "", "b"));
    EXPECT_EQ("bb", replaceAll("aaaa", "aa", "b"));
}

}  // namespace
