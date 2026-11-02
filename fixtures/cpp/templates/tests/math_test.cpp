#include <gtest/gtest.h>

#include <list>
#include <vector>

#include "meta/math.h"

namespace {

TEST(MathTest, GcdAndLcm) {
    EXPECT_EQ(6, meta::gcd(54, 24));
    EXPECT_EQ(6, meta::gcd(-54, 24));
    EXPECT_EQ(5u, meta::gcd(0u, 5u));
    EXPECT_EQ(36L, meta::lcm(12L, 18L));
    EXPECT_EQ(0, meta::lcm(0, 7));
}

TEST(MathTest, LerpAndClamp) {
    EXPECT_DOUBLE_EQ(2.5, meta::lerp(0.0, 10.0, 0.25));
    EXPECT_FLOAT_EQ(1.5f, meta::lerp(1.0f, 2.0f, 0.5f));
    EXPECT_EQ(10, meta::clamp(42, 0, 10));
    EXPECT_EQ(0, meta::clamp(-3, 0, 10));
    EXPECT_DOUBLE_EQ(0.5, meta::clamp(0.5, 0.0, 1.0));
}

TEST(MathTest, AbsDiffPicksTheRightOverload) {
    EXPECT_EQ(3u, meta::absDiff(2u, 5u));
    EXPECT_EQ(3, meta::absDiff(2, 5));
    EXPECT_EQ(7, meta::absDiff(-2, 5));
}

TEST(MathTest, SumAndMean) {
    std::vector<int> ints;
    ints.push_back(1);
    ints.push_back(2);
    ints.push_back(3);
    EXPECT_EQ(6, meta::sum(ints));
    EXPECT_DOUBLE_EQ(2.0, meta::mean(ints));
    std::list<double> doubles(4, 0.5);
    EXPECT_DOUBLE_EQ(2.0, meta::sum(doubles));
    EXPECT_DOUBLE_EQ(0.0, meta::mean(std::vector<int>()));
}

TEST(MathTest, PowerByTagDispatch) {
    EXPECT_EQ(1024, meta::power(2, 10));
    EXPECT_EQ(1, meta::power(7, 0));
    EXPECT_DOUBLE_EQ(0.125, meta::power(0.5, 3));
}

}  // namespace
