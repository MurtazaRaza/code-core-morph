#include <gtest/gtest.h>

#include <list>
#include <map>
#include <string>
#include <vector>

#include "meta/traits.h"

namespace {

struct Sized {
    int size() const { return 3; }
};

TEST(TraitsTest, HasSize) {
    EXPECT_TRUE(meta::has_size<std::vector<int> >::value);
    EXPECT_TRUE(meta::has_size<Sized>::value);
    EXPECT_FALSE(meta::has_size<int>::value);
}

TEST(TraitsTest, IsIterable) {
    EXPECT_TRUE(meta::is_iterable<std::list<int> >::value);
    EXPECT_TRUE(meta::is_iterable<std::string>::value);
    EXPECT_FALSE(meta::is_iterable<Sized>::value);
}

TEST(TraitsTest, IsContainerExcludesStrings) {
    EXPECT_TRUE((meta::is_container<std::map<int, int> >::value));
    EXPECT_TRUE(meta::is_container<std::vector<std::string> >::value);
    EXPECT_FALSE(meta::is_container<std::string>::value);
    EXPECT_FALSE(meta::is_container<const char*>::value);
    EXPECT_FALSE(meta::is_container<double>::value);
}

TEST(TraitsTest, IsPairAndIsString) {
    EXPECT_TRUE((meta::is_pair<std::pair<int, char> >::value));
    EXPECT_FALSE(meta::is_pair<int>::value);
    EXPECT_TRUE(meta::is_string<std::string>::value);
    EXPECT_TRUE(meta::is_string<const char*>::value);
    EXPECT_FALSE(meta::is_string<char>::value);
}

}  // namespace
