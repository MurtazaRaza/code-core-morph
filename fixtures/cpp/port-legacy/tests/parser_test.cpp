#include <gtest/gtest.h>

#include <stdexcept>
#include <string>

#include "calc/parser.h"

using calc::Rational;

namespace {

Rational eval(const std::string& text, const Rational& ans = Rational())
{
    calc::Expr* tree = calc::parse(text, ans);
    Rational value = tree->eval();
    delete tree;
    return value;
}

std::string shape(const std::string& text)
{
    calc::Expr* tree = calc::parse(text, Rational());
    std::string s = tree->str();
    delete tree;
    return s;
}

std::size_t error_pos(const std::string& text)
{
    try {
        delete calc::parse(text, Rational());
    } catch (const calc::ParseError& e) {
        return e.pos();
    }
    return std::string::npos;
}

} // namespace

TEST(ParserTest, Precedence)
{
    EXPECT_EQ(Rational(7), eval("1 + 2 * 3"));
    EXPECT_EQ(Rational(9), eval("(1 + 2) * 3"));
    EXPECT_EQ(Rational(1, 6), eval("1 / 2 / 3"));
    EXPECT_EQ(Rational(0), eval("1 - 2 + 1"));
    EXPECT_EQ("((1 + (2 * 3)) - 4)", shape("1 + 2*3 - 4"));
}

TEST(ParserTest, UnaryMinus)
{
    EXPECT_EQ(Rational(-6), eval("-2 * 3"));
    EXPECT_EQ(Rational(-6), eval("2 * -3"));
    EXPECT_EQ(Rational(2), eval("--2"));
    EXPECT_EQ("((-2) * 3)", shape("-2*3"));
}

TEST(ParserTest, Ans)
{
    EXPECT_EQ(Rational(7, 2), eval("ans + 1", Rational(5, 2)));
    EXPECT_EQ(Rational(0), eval("ANS"));
}

TEST(ParserTest, ErrorsCarryThePosition)
{
    EXPECT_EQ(4u, error_pos("1 + * 2"));
    EXPECT_EQ(4u, error_pos("1 + "));
    EXPECT_EQ(2u, error_pos("1 2"));
    EXPECT_EQ(0u, error_pos(")"));
    EXPECT_EQ(6u, error_pos("(1 + 2"));
    EXPECT_EQ(5u, error_pos("1 + 2)"));
    EXPECT_EQ(2u, error_pos("1 $"));
    EXPECT_EQ(0u, error_pos(""));
}

TEST(ParserTest, ErrorMessages)
{
    try {
        delete calc::parse("2 + pi", Rational());
        FAIL() << "expected calc::ParseError";
    } catch (const calc::ParseError& e) {
        EXPECT_STREQ("unknown name 'pi'", e.what());
        EXPECT_EQ(4u, e.pos());
    }
}

TEST(ParserTest, ParseErrorIsARuntimeError)
{
    EXPECT_THROW(calc::parse("(", Rational()), std::runtime_error);
}

TEST(ParserTest, DivisionByZeroIsFoundWhenEvaluating)
{
    calc::Expr* tree = calc::parse("1 / (2 - 2)", Rational());
    EXPECT_THROW(tree->eval(), std::domain_error);
    delete tree;
}
