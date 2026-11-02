#include <gtest/gtest.h>

#include "geo/shapes.h"

namespace {

void countShape(const geo::Shape& shape, void* context) {
    std::vector<std::string>* names = static_cast<std::vector<std::string>*>(context);
    names->push_back(shape.name());
}

TEST(ShapesTest, CircleMeasures) {
    geo::Circle c(geo::Point(1.0, 1.0), 2.0);
    EXPECT_NEAR(12.566370614359172, c.area(), 1e-12);
    EXPECT_NEAR(12.566370614359172, c.perimeter(), 1e-12);
    EXPECT_EQ(geo::kCircle, c.kind());
    EXPECT_EQ("circle(r=2)", c.name());
}

TEST(ShapesTest, RectangleMeasures) {
    geo::Rectangle r(geo::Point(), 3.0, 4.0);
    EXPECT_DOUBLE_EQ(12.0, r.area());
    EXPECT_DOUBLE_EQ(14.0, r.perimeter());
    EXPECT_EQ("rectangle(3x4)", r.name());
}

TEST(ShapesTest, PolygonMeasures) {
    geo::Polygon::Vertices square;
    square.push_back(geo::Point(0.0, 0.0));
    square.push_back(geo::Point(2.0, 0.0));
    square.push_back(geo::Point(2.0, 2.0));
    square.push_back(geo::Point(0.0, 2.0));
    geo::Polygon p(square);
    EXPECT_DOUBLE_EQ(4.0, p.area());
    EXPECT_DOUBLE_EQ(8.0, p.perimeter());
    EXPECT_EQ(4u, p.vertexCount());
    EXPECT_EQ("polygon(4)", p.name());
}

TEST(ShapesTest, TotalAreaAndCallbacks) {
    geo::Rectangle r(geo::Point(), 1.0, 2.0);
    geo::Rectangle s(geo::Point(), 3.0, 3.0);
    geo::ShapeRefs shapes;
    shapes.push_back(&r);
    shapes.push_back(&s);
    EXPECT_DOUBLE_EQ(11.0, geo::totalArea(shapes));

    std::vector<std::string> names;
    geo::forEachShape(shapes, countShape, &names);
    ASSERT_EQ(2u, names.size());
    EXPECT_EQ("rectangle(1x2)", names[0]);
    EXPECT_EQ("rectangle(3x3)", names[1]);
}

}  // namespace
