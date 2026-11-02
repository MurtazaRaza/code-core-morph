#ifndef GEO_VECTOR_H
#define GEO_VECTOR_H

#include <vector>

#include "geo/types.h"

namespace geo {

class Vector2 {
public:
    typedef Scalar value_type;
    typedef value_type* iterator;
    typedef const value_type* const_iterator;

    Vector2();
    Vector2(Scalar x, Scalar y);

    Scalar x() const { return v_[0]; }
    Scalar y() const { return v_[1]; }

    Scalar length() const;
    Scalar dot(const Vector2& other) const;
    Vector2 normalized() const;

    iterator begin() { return v_; }
    iterator end() { return v_ + 2; }
    const_iterator begin() const { return v_; }
    const_iterator end() const { return v_ + 2; }

    Vector2 operator+(const Vector2& other) const;
    Vector2 operator-(const Vector2& other) const;
    Vector2 operator*(Scalar factor) const;
    bool operator==(const Vector2& other) const;

private:
    value_type v_[2];
};

typedef Vector2 Point;
typedef std::vector<Point> PointList;
typedef PointList::const_iterator PointIterator;

Scalar distance(const Point& a, const Point& b);
Point centroid(const PointList& points);

}  // namespace geo

#endif  // GEO_VECTOR_H
