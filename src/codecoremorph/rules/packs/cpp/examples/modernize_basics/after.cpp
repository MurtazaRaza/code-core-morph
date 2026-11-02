#include <cstdio>

class Shape {
public:
    virtual ~Shape() = default;
    virtual double area() const = 0;
};

class Square : public Shape {
public:
    double area() const override { return side * side; }
    Shape* parent = nullptr;
    double side;
};
