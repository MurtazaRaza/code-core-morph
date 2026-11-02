#include <gtest/gtest.h>

#include <algorithm>

#include "toolkit/shuffle.h"

namespace {

std::vector<int> range(int n) {
    std::vector<int> v;
    for (int i = 0; i < n; ++i) {
        v.push_back(i);
    }
    return v;
}

TEST(ShuffleTest, IsAPermutation) {
    std::vector<int> out = toolkit::shuffled(range(20), 7);
    ASSERT_EQ(20u, out.size());
    std::vector<int> sorted(out);
    std::sort(sorted.begin(), sorted.end());
    EXPECT_EQ(range(20), sorted);
    EXPECT_NE(range(20), out);
}

TEST(ShuffleTest, IsDeterministicPerSeed) {
    EXPECT_EQ(toolkit::shuffled(range(10), 42), toolkit::shuffled(range(10), 42));
}

TEST(ShuffleTest, DealsEveryCard) {
    std::vector<int> totals = toolkit::deal(12, 3, 1);
    ASSERT_EQ(3u, totals.size());
    EXPECT_EQ(66, totals[0] + totals[1] + totals[2]);
}

TEST(ShuffleTest, FastSum) {
    int values[] = {1, 2, 3, 4};
    EXPECT_EQ(10, toolkit::fastSum(values, 4));
    EXPECT_EQ(0, toolkit::fastSum(values, 0));
}

}  // namespace
