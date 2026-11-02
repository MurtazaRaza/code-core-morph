#include <gtest/gtest.h>

#include "pkgmeta/manifest.h"

using pkgmeta::parse_manifest;
using pkgmeta::ParseOptions;

namespace {

const char* const kManifest =
    "# demo package\n"
    "name: app\n"
    "version: 1.2.0\n"
    "requires: json >=3.0\n"
    "requires: log\n"
    "provides: app-cli\n"
    "homepage: https://example.invalid\n";

} // namespace

TEST(ManifestTest, ParsesEveryKey)
{
    auto result = parse_manifest(kManifest, ParseOptions{});
    ASSERT_TRUE(result.manifest.has_value()) << result.error;
    const pkgmeta::Manifest& manifest = *result.manifest;
    EXPECT_EQ("app", manifest.name);
    EXPECT_EQ("1.2.0", manifest.version.to_string());
    ASSERT_EQ(2u, manifest.requires.size());
    EXPECT_EQ("json", manifest.requires[0].name);
    EXPECT_EQ(">=3.0", manifest.requires[0].constraint);
    EXPECT_EQ("log", manifest.requires[1].name);
    EXPECT_EQ("*", manifest.requires[1].constraint);
    ASSERT_EQ(1u, manifest.provides.size());
    EXPECT_EQ("app-cli", manifest.provides[0]);
}

TEST(ManifestTest, StrictModeRejectsUnknownKeys)
{
    auto result = parse_manifest(kManifest, ParseOptions{true, 8});
    EXPECT_FALSE(result.manifest.has_value());
    EXPECT_EQ("line 7: unknown key 'homepage'", result.error);
}

TEST(ManifestTest, DependencyLimit)
{
    auto result = parse_manifest(kManifest, ParseOptions{false, 1});
    EXPECT_FALSE(result.manifest.has_value());
    EXPECT_EQ("line 5: too many dependencies", result.error);
}

TEST(ManifestTest, Errors)
{
    EXPECT_EQ("missing name", parse_manifest("version: 1\n", {}).error);
    EXPECT_EQ("missing version", parse_manifest("name: a\n", {}).error);
    EXPECT_EQ("line 2: bad version '1.x'", parse_manifest("name: a\nversion: 1.x\n", {}).error);
    EXPECT_EQ("line 1: expected 'key: value'", parse_manifest("name a\n", {}).error);
}

TEST(ManifestTest, CrLfAndBlankLines)
{
    auto result = parse_manifest("\r\nname: a\r\n\r\nversion: 2\r\n", {});
    ASSERT_TRUE(result.manifest.has_value()) << result.error;
    EXPECT_EQ("a", result.manifest->name);
}
