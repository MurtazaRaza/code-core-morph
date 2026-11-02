#ifndef GEO_SHAPES_H
#define GEO_SHAPES_H

#include <string>
#include <vector>

#include "geo/types.h"
#include "geo/vector.h"

namespace geo {

enum ShapeKind { kCircle, kRectangle, kPolygon };

class Shape {
public:
    typedef ShapeKind Kind;

    explicit Shape(Kind kind) : kind_(kind) {}
    virtual ~Shape() {}

    Kind kind() const { return kind_; }
    virtual Scalar area() const = 0;
    virtual Scalar perimeter() const = 0;
    virtual std::string name() const = 0;

private:
    Kind kind_;
};

class Circle : public Shape {
public:
    Circle(const Point& center, Scalar radius);
    Scalar area() const;
    Scalar perimeter() const;
    std::string name() const;
    Scalar radius() const { return radius_; }

private:
    Point center_;
    Scalar radius_;
};

class Rectangle : public Shape {
public:
    Rectangle(const Point& corner, Scalar width, Scalar height);
    Scalar area() const;
    Scalar perimeter() const;
    std::string name() const;

private:
    Point corner_;
    Scalar width_;
    Scalar height_;
};

class Polygon : public Shape {
public:
    typedef PointList Vertices;

    explicit Polygon(const Vertices& vertices);
    Scalar area() const;
    Scalar perimeter() const;
    std::string name() const;
    Index vertexCount() const { return vertices_.size(); }

private:
    Vertices vertices_;
};

typedef std::vector<const Shape*> ShapeRefs;
typedef void (*ShapeCallback)(const Shape& shape, void* context);

Scalar totalArea(const ShapeRefs& shapes);
void forEachShape(const ShapeRefs& shapes, ShapeCallback callback, void* context);

}  // namespace geo

#endif  // GEO_SHAPES_H
