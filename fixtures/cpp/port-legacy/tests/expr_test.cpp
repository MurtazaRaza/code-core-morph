#include <gtest/gtest.h>

#include <stdexcept>
#include <vector>

#include "calc/expr.h"

using calc::Binary;
using calc::Expr;
using calc::Negate;
using calc::Number;
using calc::Rational;

namespace {

// A leaf defined outside the library, counting how often it is evaluated.
class Counted : public Expr
{
public:
    Counted(int value, int* evals) : value_(value), evals_(evals) {}

    Rational eval() const
    {
        ++*evals_;
        return Rational(value_);
    }
    std::string str() const { return "#"; }
    Expr* clone() const { return new Counted(value_, evals_); }

private:
    int value_;
    int* evals_;
};

} // namespace

TEST(ExprTest, EvaluatesATree)
{
    Binary tree('*', new Binary('+', new Number(Rational(1, 2)), new Number(Rational(3))),
                new Negate(new Number(Rational(2))));
    EXPECT_EQ(Rational(-7), tree.eval());
    EXPECT_EQ("((1/2 + 3) * (-2))", tree.str());
    EXPECT_EQ(6, tree.size());
    EXPECT_EQ('*', tree.op());
}

TEST(ExprTest, DivisionByZeroThrows)
{
    Binary tree('/', new Number(Rational(1)),
                new Binary('-', new Number(Rational(2)), new Number(Rational(2))));
    EXPECT_THROW(tree.eval(), std::domain_error);
}

TEST(ExprTest, UnknownOperatorThrows)
{
    EXPECT_THROW(Binary('%', new Number(Rational(1)), new Number(Rational(2))),
                 std::invalid_argument);
}

TEST(ExprTest, CloneIsDeep)
{
    Expr* original = new Binary('-', new Number(Rational(5)), new Number(Rational(3)));
    Expr* copy = original->clone();
    delete original;
    EXPECT_EQ(Rational(2), copy->eval());
    EXPECT_EQ("(5 - 3)", copy->str());
    delete copy;
}

TEST(ExprTest, WorksThroughTheBaseClass)
{
    std::vector<Expr*> exprs;
    exprs.push_back(new Number(Rational(4)));
    exprs.push_back(new Negate(new Number(Rational(1, 2))));
    exprs.push_back(new Binary('/', new Number(Rational(1)), new Number(Rational(4))));
    Rational total;
    int nodes = 0;
    for (std::vector<Expr*>::size_type i = 0; i < exprs.size(); ++i) {
        total += exprs[i]->eval();
        nodes += exprs[i]->size();
    }
    EXPECT_EQ(Rational(15, 4), total);
    EXPECT_EQ(6, nodes);
    for (std::vector<Expr*>::size_type i = 0; i < exprs.size(); ++i)
        delete exprs[i];
}

TEST(ExprTest, SubclassesWorkAsChildren)
{
    int evals = 0;
    Binary tree('+', new Counted(2, &evals), new Negate(new Counted(5, &evals)));
    EXPECT_EQ(Rational(-3), tree.eval());
    EXPECT_EQ(2, evals);
    EXPECT_EQ("(# + (-#))", tree.str());
    EXPECT_EQ(4, tree.size());
    Expr* copy = tree.clone();
    EXPECT_EQ(Rational(-3), copy->eval());
    EXPECT_EQ(4, evals);
    delete copy;
}
