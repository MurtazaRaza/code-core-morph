#include <gtest/gtest.h>

#include "pkgmeta/version.h"

using pkgmeta::semver::parse_version;
using pkgmeta::semver::satisfies;
using pkgmeta::semver::Version;

TEST(VersionTest, ParsesFullAndShortForms)
{
    auto full = parse_version("1.2.3");
    ASSERT_TRUE(full.has_value());
    EXPECT_EQ(1, full->major());
    EXPECT_EQ(2, full->minor());
    EXPECT_EQ(3, full->patch());
    EXPECT_EQ("2.0.0", parse_version("2")->to_string());
    EXPECT_EQ("2.5.0", parse_version("2.5")->to_string());
}

TEST(VersionTest, RejectsMalformedVersions)
{
    for (const char* bad : {"", "1.", ".1", "1..2", "1.2.3.4", "1.2.x", "-1.0", "v1"})
        EXPECT_FALSE(parse_version(bad).has_value()) << bad;
}

TEST(VersionTest, Comparison)
{
    Version a(1, 2, 3);
    Version b(1, 10, 0);
    EXPECT_TRUE(a < b);
    EXPECT_FALSE(b < a);
    EXPECT_TRUE(a == Version(1, 2, 3));
    EXPECT_TRUE(a != b);
    EXPECT_EQ(0, a.compare(Version(1, 2, 3)));
    EXPECT_EQ(Version().to_string(), "0.0.0");
}

TEST(VersionTest, Constraints)
{
    Version v(1, 4, 2);
    EXPECT_TRUE(satisfies(v, "*"));
    EXPECT_TRUE(satisfies(v, ">=1.4"));
    EXPECT_FALSE(satisfies(v, ">1.4.2"));
    EXPECT_TRUE(satisfies(v, "<2"));
    EXPECT_TRUE(satisfies(v, "<=1.4.2"));
    EXPECT_TRUE(satisfies(v, "=1.4.2"));
    EXPECT_TRUE(satisfies(v, "1.4.2"));
    EXPECT_TRUE(satisfies(v, "^1.2"));
    EXPECT_FALSE(satisfies(v, "^2.0"));
    EXPECT_FALSE(satisfies(v, "^1.5"));
    EXPECT_FALSE(satisfies(v, ">=banana"));
}
