#ifndef CALC_EXPR_H
#define CALC_EXPR_H

#include <string>

#include "calc/rational.h"

namespace calc {

// An expression tree. A node owns its children and deletes them.
class Expr
{
public:
    virtual ~Expr();

    // Throws std::domain_error on a division by zero.
    virtual Rational eval() const = 0;
    // Fully parenthesized: "((1/2 + 3) * (-2))".
    virtual std::string str() const = 0;
    // The number of nodes in the tree.
    virtual int size() const;
    // A deep copy, which the caller deletes.
    virtual Expr* clone() const = 0;
};

class Number : public Expr
{
public:
    explicit Number(const Rational& value);

    Rational eval() const;
    std::string str() const;
    Expr* clone() const;

private:
    Rational value_;
};

class Negate : public Expr
{
public:
    // Takes ownership of operand.
    explicit Negate(Expr* operand);
    ~Negate();

    Rational eval() const;
    std::string str() const;
    int size() const;
    Expr* clone() const;

private:
    Negate(const Negate&);
    Negate& operator=(const Negate&);

    Expr* operand_;
};

class Binary : public Expr
{
public:
    // Takes ownership of lhs and rhs, and deletes them if op isn't one of + - * /, in which
    // case it throws std::invalid_argument.
    Binary(char op, Expr* lhs, Expr* rhs);
    ~Binary();

    Rational eval() const;
    std::string str() const;
    int size() const;
    Expr* clone() const;

    char op() const { return op_; }

private:
    Binary(const Binary&);
    Binary& operator=(const Binary&);

    char op_;
    Expr* lhs_;
    Expr* rhs_;
};

} // namespace calc

#endif
