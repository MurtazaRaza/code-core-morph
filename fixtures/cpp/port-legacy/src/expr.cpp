#include "calc/expr.h"

#include <stdexcept>

namespace calc {

Expr::~Expr()
{
}

int Expr::size() const
{
    return 1;
}

Number::Number(const Rational& value) : value_(value)
{
}

Rational Number::eval() const
{
    return value_;
}

std::string Number::str() const
{
    return value_.str();
}

Expr* Number::clone() const
{
    return new Number(value_);
}

Negate::Negate(Expr* operand) : operand_(operand)
{
}

Negate::~Negate()
{
    delete operand_;
}

Rational Negate::eval() const
{
    return -operand_->eval();
}

std::string Negate::str() const
{
    return "(-" + operand_->str() + ")";
}

int Negate::size() const
{
    return 1 + operand_->size();
}

Expr* Negate::clone() const
{
    return new Negate(operand_->clone());
}

Binary::Binary(char op, Expr* lhs, Expr* rhs) : op_(op), lhs_(lhs), rhs_(rhs)
{
    if (op != '+' && op != '-' && op != '*' && op != '/') {
        delete lhs;
        delete rhs;
        throw std::invalid_argument("unknown operator");
    }
}

Binary::~Binary()
{
    delete lhs_;
    delete rhs_;
}

Rational Binary::eval() const
{
    Rational left = lhs_->eval();
    Rational right = rhs_->eval();
    switch (op_) {
    case '+':
        return left + right;
    case '-':
        return left - right;
    case '*':
        return left * right;
    default:
        return left / right;
    }
}

std::string Binary::str() const
{
    return "(" + lhs_->str() + " " + op_ + " " + rhs_->str() + ")";
}

int Binary::size() const
{
    return 1 + lhs_->size() + rhs_->size();
}

Expr* Binary::clone() const
{
    Expr* lhs = lhs_->clone();
    Expr* rhs = 0;
    try {
        rhs = rhs_->clone();
    } catch (...) {
        delete lhs;
        throw;
    }
    return new Binary(op_, lhs, rhs);
}

} // namespace calc
