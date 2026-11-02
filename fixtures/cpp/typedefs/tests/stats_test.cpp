#include <gtest/gtest.h>

#include <list>

#include "geo/stats.h"

namespace {

typedef std::list<int> IntList;

void accumulate(geo::Scalar value, void* context) { *static_cast<geo::Scalar*>(context) += value; }

TEST(StatsTest, SummarizesVectors) {
    geo::ScalarList values;
    values.push_back(2.0);
    values.push_back(4.0);
    values.push_back(4.0);
    values.push_back(4.0);
    values.push_back(5.0);
    values.push_back(5.0);
    values.push_back(7.0);
    values.push_back(9.0);
    geo::Summary<geo::ScalarList> s = geo::summarize(values);
    EXPECT_EQ(8u, s.count);
    EXPECT_DOUBLE_EQ(2.0, s.minimum);
    EXPECT_DOUBLE_EQ(9.0, s.maximum);
    EXPECT_DOUBLE_EQ(5.0, s.mean);
    EXPECT_DOUBLE_EQ(2.0, s.stddev);
}

TEST(StatsTest, SummarizesListsAndEmpty) {
    IntList values;
    geo::Summary<IntList> empty = geo::summarize(values);
    EXPECT_EQ(0u, empty.count);
    values.push_back(3);
    values.push_back(1);
    geo::Summary<IntList> s = geo::summarize(values);
    EXPECT_EQ(1, s.minimum);
    EXPECT_EQ(3, s.maximum);
    EXPECT_DOUBLE_EQ(2.0, s.mean);
}

TEST(StatsTest, SortsAndMedians) {
    geo::ScalarList values;
    values.push_back(3.0);
    values.push_back(1.0);
    values.push_back(2.0);
    geo::ScalarList down = geo::sorted(values, geo::descending);
    EXPECT_DOUBLE_EQ(3.0, down.front());
    EXPECT_DOUBLE_EQ(2.0, geo::median(values));
    values.push_back(10.0);
    EXPECT_DOUBLE_EQ(2.5, geo::median(values));
    EXPECT_DOUBLE_EQ(0.0, geo::median(geo::ScalarList()));
}

TEST(StatsTest, VisitsEveryValue) {
    geo::ScalarList values(3, 1.5);
    geo::Scalar total = 0.0;
    geo::visitAll(values, accumulate, &total);
    EXPECT_DOUBLE_EQ(4.5, total);
}

TEST(WindowTest, KeepsTheLastN) {
    geo::Window<int, 3> window;
    for (int i = 1; i <= 5; ++i) {
        window.push(i);
    }
    EXPECT_TRUE(window.full());
    geo::Window<int, 3>::Storage values = window.values();
    ASSERT_EQ(3u, values.size());
    EXPECT_EQ(3, values[0]);
    EXPECT_EQ(4, values[1]);
    EXPECT_EQ(5, values[2]);
}

}  // namespace
