#include "geo/vector.h"

#include <cmath>

namespace geo {

namespace {
typedef Scalar (*UnaryOp)(Scalar);

Scalar square(Scalar v) { return v * v; }

Scalar apply(UnaryOp op, Scalar v) { return op(v); }
}  // namespace

Vector2::Vector2() {
    v_[0] = 0.0;
    v_[1] = 0.0;
}

Vector2::Vector2(Scalar x, Scalar y) {
    v_[0] = x;
    v_[1] = y;
}

Scalar Vector2::length() const {
    return std::sqrt(apply(square, v_[0]) + apply(square, v_[1]));
}

Scalar Vector2::dot(const Vector2& other) const {
    return v_[0] * other.v_[0] + v_[1] * other.v_[1];
}

Vector2 Vector2::normalized() const {
    Scalar len = length();
    if (len == 0.0) {
        return Vector2();
    }
    return Vector2(v_[0] / len, v_[1] / len);
}

Vector2 Vector2::operator+(const Vector2& other) const {
    return Vector2(v_[0] + other.v_[0], v_[1] + other.v_[1]);
}

Vector2 Vector2::operator-(const Vector2& other) const {
    return Vector2(v_[0] - other.v_[0], v_[1] - other.v_[1]);
}

Vector2 Vector2::operator*(Scalar factor) const {
    return Vector2(v_[0] * factor, v_[1] * factor);
}

bool Vector2::operator==(const Vector2& other) const {
    return v_[0] == other.v_[0] && v_[1] == other.v_[1];
}

Scalar distance(const Point& a, const Point& b) { return (a - b).length(); }

Point centroid(const PointList& points) {
    if (points.empty()) {
        return Point();
    }
    Scalar x = 0.0;
    Scalar y = 0.0;
    for (PointIterator it = points.begin(); it != points.end(); ++it) {
        x += it->x();
        y += it->y();
    }
    Scalar n = static_cast<Scalar>(points.size());
    return Point(x / n, y / n);
}

}  // namespace geo
