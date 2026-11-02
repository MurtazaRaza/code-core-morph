#include <gtest/gtest.h>

#include "geo/vector.h"

namespace {

typedef geo::Vector2 V;

TEST(VectorTest, DefaultIsOrigin) {
    V v;
    EXPECT_DOUBLE_EQ(0.0, v.x());
    EXPECT_DOUBLE_EQ(0.0, v.y());
}

TEST(VectorTest, LengthAndDot) {
    V v(3.0, 4.0);
    EXPECT_DOUBLE_EQ(5.0, v.length());
    EXPECT_DOUBLE_EQ(11.0, v.dot(V(1.0, 2.0)));
}

TEST(VectorTest, Normalized) {
    V n = V(0.0, 2.0).normalized();
    EXPECT_DOUBLE_EQ(0.0, n.x());
    EXPECT_DOUBLE_EQ(1.0, n.y());
    EXPECT_TRUE(V().normalized() == V());
}

TEST(VectorTest, Arithmetic) {
    V a(1.0, 2.0);
    V b(3.0, 5.0);
    EXPECT_TRUE(a + b == V(4.0, 7.0));
    EXPECT_TRUE(b - a == V(2.0, 3.0));
    EXPECT_TRUE(a * 2.0 == V(2.0, 4.0));
}

TEST(VectorTest, Iterates) {
    V v(1.5, 2.5);
    geo::Scalar sum = 0.0;
    for (V::const_iterator it = v.begin(); it != v.end(); ++it) {
        sum += *it;
    }
    EXPECT_DOUBLE_EQ(4.0, sum);
}

TEST(PointTest, DistanceAndCentroid) {
    geo::PointList points;
    points.push_back(geo::Point(0.0, 0.0));
    points.push_back(geo::Point(4.0, 0.0));
    points.push_back(geo::Point(4.0, 3.0));
    EXPECT_DOUBLE_EQ(5.0, geo::distance(points[0], points[2]));
    geo::Point c = geo::centroid(points);
    EXPECT_NEAR(8.0 / 3.0, c.x(), 1e-12);
    EXPECT_NEAR(1.0, c.y(), 1e-12);
    EXPECT_TRUE(geo::centroid(geo::PointList()) == geo::Point());
}

}  // namespace
