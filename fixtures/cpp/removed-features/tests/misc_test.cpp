#include <gtest/gtest.h>

#include <cstring>

#include "toolkit/misc.h"

namespace {

TEST(MiscTest, KeywordNamedFunctions) {
    EXPECT_TRUE(toolkit::concept("Graph"));
    EXPECT_FALSE(toolkit::concept("graph"));
    EXPECT_FALSE(toolkit::concept(""));
    EXPECT_EQ(7, toolkit::requires(3));
}

TEST(MiscTest, Utf8Greeting) { EXPECT_EQ(0, std::strcmp("hello, world", toolkit::greeting())); }

TEST(MiscTest, AggregateRanges) {
    toolkit::Range r = {2, 5};
    toolkit::Range w = toolkit::widen(r, 1);
    EXPECT_EQ(1, w.low);
    EXPECT_EQ(6, w.high);
    EXPECT_EQ(5, toolkit::span(w));
}

TEST(MiscTest, VersionComparison) {
    toolkit::Version a = {1, 2};
    toolkit::Version b = {1, 2};
    toolkit::Version c = {2, 0};
    EXPECT_TRUE(a == b);
    EXPECT_TRUE(a != c);
}

TEST(MiscTest, CounterCapturesThis) {
    toolkit::Counter counter;
    std::vector<int> values(4, 5);
    counter.addAll(values);
    EXPECT_EQ(20, counter.total());
}

}  // namespace
