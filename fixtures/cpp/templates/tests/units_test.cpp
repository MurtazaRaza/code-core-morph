#include <gtest/gtest.h>

#include "meta/units.h"

namespace {

typedef meta::Measure<meta::Meters> Distance;
typedef meta::Measure<meta::Seconds> Duration;

TEST(UnitsTest, AddsSameUnits) {
    Distance a(1.5);
    Distance b(2.25);
    EXPECT_DOUBLE_EQ(3.75, (a + b).value());
    EXPECT_DOUBLE_EQ(0.75, (b - a).value());
    EXPECT_TRUE(a < b);
}

TEST(UnitsTest, DividesDistanceByTime) {
    meta::Measure<meta::MetersPerSecond> speed = Distance(100.0) / Duration(8.0);
    EXPECT_DOUBLE_EQ(12.5, speed.value());
    EXPECT_EQ("12.50 m/s", speed.str());
}

TEST(UnitsTest, Formats) {
    EXPECT_EQ("3.00 m", Distance(3.0).str());
    EXPECT_EQ("0.50 s", Duration(0.5).str());
}

}  // namespace
