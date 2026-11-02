#include <gtest/gtest.h>

#include "toolkit/functional.h"

namespace {

std::vector<int> sample() {
    std::vector<int> v;
    v.push_back(1);
    v.push_back(2);
    v.push_back(3);
    v.push_back(-4);
    return v;
}

TEST(FunctionalTest, BindersScaleAndOffset) {
    std::vector<int> s = toolkit::scaled(sample(), 3);
    EXPECT_EQ(9, s[2]);
    EXPECT_EQ(-12, s[3]);
    std::vector<int> o = toolkit::offset(sample(), 10);
    EXPECT_EQ(11, o[0]);
    EXPECT_EQ(6, o[3]);
}

TEST(FunctionalTest, NegatorsAndBaseClasses) {
    EXPECT_EQ(2, toolkit::countOdd(sample()));
    EXPECT_EQ(2, toolkit::countBelow(sample(), 3));
    EXPECT_TRUE(toolkit::IsEven()(4));
}

TEST(FunctionalTest, PtrFunAndMemFunRef) {
    EXPECT_EQ("eoo", toolkit::vowels("Hello World"));
    std::vector<toolkit::Entry> entries;
    toolkit::Entry a = {"alpha", 1};
    toolkit::Entry b = {"beta", 2};
    entries.push_back(a);
    entries.push_back(b);
    std::vector<std::string> n = toolkit::names(entries);
    ASSERT_EQ(2u, n.size());
    EXPECT_EQ("beta", n[1]);
}

}  // namespace
