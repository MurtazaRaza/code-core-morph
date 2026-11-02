#include <stdio.h>

class Shape {
public:
    virtual ~Shape() {}
    virtual double area(void) const = 0;
};

class Square : public Shape {
public:
    virtual double area(void) const { return side * side; }
    Shape* parent = NULL;
    double side;
};
