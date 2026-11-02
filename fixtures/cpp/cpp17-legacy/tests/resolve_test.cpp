#include <gtest/gtest.h>

#include "pkgmeta/resolve.h"

using pkgmeta::Manifest;
using pkgmeta::Registry;
using pkgmeta::resolve;
using pkgmeta::ResolveError;

namespace {

Manifest package(const std::string& name, const std::string& version,
                 std::vector<pkgmeta::Dependency> requires = {})
{
    Manifest manifest;
    manifest.name = name;
    manifest.version = *pkgmeta::semver::parse_version(version);
    manifest.requires = std::move(requires);
    return manifest;
}

Registry sample()
{
    Registry registry;
    registry["app"] = package("app", "1.0", {{"log", "*"}, {"json", ">=3"}});
    registry["json"] = package("json", "3.1", {{"log", "^1.0"}});
    registry["log"] = package("log", "1.4.0");
    return registry;
}

} // namespace

TEST(ResolveTest, DependenciesComeFirst)
{
    std::vector<std::string> trace;
    EXPECT_EQ((std::vector<std::string>{"log", "json", "app"}), resolve(sample(), "app", &trace));
    EXPECT_EQ("visit app", trace.front());
    EXPECT_EQ("done app", trace.back());
}

TEST(ResolveTest, MissingPackage)
{
    Registry registry = sample();
    registry.erase("log");
    std::vector<std::string> trace;
    EXPECT_THROW(resolve(registry, "app", &trace), ResolveError);
    EXPECT_EQ("aborted", trace.back());
}

TEST(ResolveTest, UnsatisfiedConstraint)
{
    Registry registry = sample();
    registry["log"] = package("log", "2.0");
    try {
        resolve(registry, "app");
        FAIL() << "expected ResolveError";
    } catch (const ResolveError& error) {
        EXPECT_STREQ("json needs log ^1.0, found 2.0.0", error.what());
    }
}

TEST(ResolveTest, Cycle)
{
    Registry registry;
    registry["a"] = package("a", "1", {{"b", "*"}});
    registry["b"] = package("b", "1", {{"a", "*"}});
    EXPECT_THROW(resolve(registry, "a"), ResolveError);
}

TEST(ResolveTest, NoTrace)
{
    EXPECT_EQ(1u, resolve(sample(), "log").size());
}
