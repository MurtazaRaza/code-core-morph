#include <gtest/gtest.h>

#include "pkgmeta/render.h"

using pkgmeta::Report;

TEST(RenderTest, Tree)
{
    pkgmeta::Manifest manifest;
    manifest.name = "app";
    manifest.version = pkgmeta::semver::Version(1, 2, 0);
    manifest.requires.push_back({"json", ">=3.0"});
    manifest.requires.push_back({"log", "*"});
    EXPECT_EQ("app 1.2.0\n"
              "\xE2\x94\x9C\xE2\x94\x80 json >=3.0\n"
              "\xE2\x94\x94\xE2\x94\x80 log *\n",
              pkgmeta::render_tree(manifest));
}

TEST(RenderTest, TreeWithoutDependencies)
{
    pkgmeta::Manifest manifest;
    manifest.name = "leaf";
    EXPECT_EQ("leaf 0.0.0\n", pkgmeta::render_tree(manifest));
}

TEST(RenderTest, MapValues)
{
    std::map<std::string, int> sizes{{"a", 1}, {"b", 22}};
    auto labels = pkgmeta::map_values(sizes, [](const int& n) { return std::to_string(n * 2); });
    ASSERT_EQ(2u, labels.size());
    EXPECT_EQ("2", labels["a"]);
    EXPECT_EQ("44", labels["b"]);
}

TEST(RenderTest, ReportPrefixesLines)
{
    Report report("  ");
    report.add_all({"one", "", "two"});
    ASSERT_EQ(3u, report.lines().size());
    EXPECT_EQ("  one", report.lines()[0]);
    EXPECT_EQ("  one\n  \n  two\n", report.text());
}

TEST(RenderTest, AppendLineToATemporary)
{
    EXPECT_TRUE(Report::append_line(pkgmeta::as_lvalue(std::string("x")), "y"));
    EXPECT_FALSE(Report::append_line(pkgmeta::as_lvalue(std::string()), ""));
}
