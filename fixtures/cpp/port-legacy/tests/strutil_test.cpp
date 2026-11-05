#include <gtest/gtest.h>

#include <string>
#include <vector>

#include "calc/strutil.h"

namespace strutil = calc::strutil;

TEST(StrutilTest, TrimRemovesSpaceAtBothEnds)
{
    EXPECT_EQ("a b", strutil::trim(" \t a b \r\n"));
    EXPECT_EQ("", strutil::trim("   "));
    EXPECT_EQ("", strutil::trim(""));
    EXPECT_EQ("x", strutil::trim("x"));
}

TEST(StrutilTest, SplitKeepsEmptyFields)
{
    std::vector<std::string> fields = strutil::split("a,,b", ',');
    ASSERT_EQ(3u, fields.size());
    EXPECT_EQ("a", fields[0]);
    EXPECT_EQ("", fields[1]);
    EXPECT_EQ("b", fields[2]);
    EXPECT_EQ(1u, strutil::split("", ',').size());
    EXPECT_EQ(2u, strutil::split("a,", ',').size());
}

TEST(StrutilTest, JoinPutsTheSeparatorBetween)
{
    std::vector<std::string> parts;
    EXPECT_EQ("", strutil::join(parts, ", "));
    parts.push_back("a");
    EXPECT_EQ("a", strutil::join(parts, ", "));
    parts.push_back("b");
    parts.push_back("c");
    EXPECT_EQ("a, b, c", strutil::join(parts, ", "));
}

TEST(StrutilTest, ToLowerChangesAsciiLettersOnly)
{
    EXPECT_EQ("hello, world 42", strutil::to_lower("HeLLo, World 42"));
    // Not UTF-8, and kept byte for byte.
    EXPECT_EQ("\xff" "abc\xc3", strutil::to_lower("\xff" "ABC\xc3"));
}

TEST(StrutilTest, StartsWith)
{
    EXPECT_TRUE(strutil::starts_with("calc", "ca"));
    EXPECT_TRUE(strutil::starts_with("calc", ""));
    EXPECT_FALSE(strutil::starts_with("ca", "calc"));
    EXPECT_FALSE(strutil::starts_with("calc", "Ca"));
}

TEST(StrutilTest, ReplaceAllCountsReplacements)
{
    std::string s = "a-b-c";
    EXPECT_EQ(2, strutil::replace_all(s, "-", "--"));
    EXPECT_EQ("a--b--c", s);
    std::string t = "aaa";
    EXPECT_EQ(1, strutil::replace_all(t, "aa", "b"));
    EXPECT_EQ("ba", t);
    std::string u = "abc";
    EXPECT_EQ(0, strutil::replace_all(u, "x", "y"));
    EXPECT_EQ("abc", u);
}
