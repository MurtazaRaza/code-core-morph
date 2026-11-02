#include "geo/shapes.h"

#include <cmath>
#include <sstream>

namespace geo {

namespace {
const Scalar kPi = 3.14159265358979323846;

typedef std::ostringstream Stream;
}  // namespace

Circle::Circle(const Point& center, Scalar radius)
    : Shape(kCircle), center_(center), radius_(radius) {}

Scalar Circle::area() const { return kPi * radius_ * radius_; }

Scalar Circle::perimeter() const { return 2.0 * kPi * radius_; }

std::string Circle::name() const {
    Stream out;
    out << "circle(r=" << radius_ << ")";
    return out.str();
}

Rectangle::Rectangle(const Point& corner, Scalar width, Scalar height)
    : Shape(kRectangle), corner_(corner), width_(width), height_(height) {}

Scalar Rectangle::area() const { return width_ * height_; }

Scalar Rectangle::perimeter() const { return 2.0 * (width_ + height_); }

std::string Rectangle::name() const {
    Stream out;
    out << "rectangle(" << width_ << "x" << height_ << ")";
    return out.str();
}

Polygon::Polygon(const Vertices& vertices) : Shape(kPolygon), vertices_(vertices) {}

Scalar Polygon::area() const {
    // Shoelace formula.
    Scalar twice = 0.0;
    Index n = vertices_.size();
    for (Index i = 0; i < n; ++i) {
        const Point& a = vertices_[i];
        const Point& b = vertices_[(i + 1) % n];
        twice += a.x() * b.y() - b.x() * a.y();
    }
    return std::fabs(twice) / 2.0;
}

Scalar Polygon::perimeter() const {
    Scalar total = 0.0;
    Index n = vertices_.size();
    for (Index i = 0; i < n; ++i) {
        total += distance(vertices_[i], vertices_[(i + 1) % n]);
    }
    return total;
}

std::string Polygon::name() const {
    Stream out;
    out << "polygon(" << vertices_.size() << ")";
    return out.str();
}

Scalar totalArea(const ShapeRefs& shapes) {
    Scalar total = 0.0;
    for (ShapeRefs::const_iterator it = shapes.begin(); it != shapes.end(); ++it) {
        total += (*it)->area();
    }
    return total;
}

void forEachShape(const ShapeRefs& shapes, ShapeCallback callback, void* context) {
    for (ShapeRefs::const_iterator it = shapes.begin(); it != shapes.end(); ++it) {
        callback(**it, context);
    }
}

}  // namespace geo
