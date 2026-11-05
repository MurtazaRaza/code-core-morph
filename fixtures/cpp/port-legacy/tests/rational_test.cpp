#include <gtest/gtest.h>

#include <sstream>
#include <stdexcept>

#include "calc/rational.h"

using calc::Rational;

TEST(RationalTest, NormalizesToLowestTerms)
{
    Rational r(6, -8);
    EXPECT_EQ(-3, r.num());
    EXPECT_EQ(4, r.den());
    EXPECT_EQ(0, Rational(0, -5).num());
    EXPECT_EQ(1, Rational(0, -5).den());
    EXPECT_TRUE(Rational(4, 2).is_integer());
    EXPECT_FALSE(Rational(1, 2).is_integer());
}

TEST(RationalTest, ZeroDenominatorThrows)
{
    EXPECT_THROW(Rational(1, 0), std::domain_error);
}

TEST(RationalTest, Arithmetic)
{
    EXPECT_EQ(Rational(5, 6), Rational(1, 2) + Rational(1, 3));
    EXPECT_EQ(Rational(1, 6), Rational(1, 2) - Rational(1, 3));
    EXPECT_EQ(Rational(1, 6), Rational(1, 2) * Rational(1, 3));
    EXPECT_EQ(Rational(3, 2), Rational(1, 2) / Rational(1, 3));
    EXPECT_EQ(Rational(-1, 2), -Rational(1, 2));
    Rational r(1, 4);
    r += Rational(1, 4);
    r *= Rational(4);
    r -= Rational(1, 2);
    EXPECT_EQ(Rational(3, 2), r);
}

TEST(RationalTest, DivisionByZeroThrowsAndKeepsTheValue)
{
    Rational r(3, 4);
    try {
        r /= Rational(0);
        FAIL() << "expected std::domain_error";
    } catch (const std::domain_error& e) {
        EXPECT_STREQ("division by zero", e.what());
    }
    EXPECT_EQ(Rational(3, 4), r);
}

TEST(RationalTest, Comparison)
{
    EXPECT_TRUE(Rational(1, 3) < Rational(1, 2));
    EXPECT_TRUE(Rational(-1, 2) < Rational(1, 3));
    EXPECT_TRUE(Rational(1, 2) <= Rational(2, 4));
    EXPECT_TRUE(Rational(1, 2) != Rational(1, 3));
    EXPECT_FALSE(Rational(1, 2) < Rational(1, 2));
}

TEST(RationalTest, TruncateAndToDouble)
{
    EXPECT_EQ(3, Rational(7, 2).truncate());
    EXPECT_EQ(-3, Rational(-7, 2).truncate());
    EXPECT_DOUBLE_EQ(0.75, Rational(3, 4).to_double());
}

TEST(RationalTest, StrAndStream)
{
    EXPECT_EQ("7/2", Rational(7, 2).str());
    EXPECT_EQ("-1/3", Rational(1, -3).str());
    EXPECT_EQ("4", Rational(8, 2).str());
    std::ostringstream out;
    out << Rational(2, 3) << ' ' << Rational(5);
    EXPECT_EQ("2/3 5", out.str());
}

TEST(RationalTest, Parse)
{
    Rational r;
    EXPECT_TRUE(Rational::parse(" 1 / 4 ", r));
    EXPECT_EQ(Rational(1, 4), r);
    EXPECT_TRUE(Rational::parse("-7/2", r));
    EXPECT_EQ(Rational(-7, 2), r);
    EXPECT_TRUE(Rational::parse("3", r));
    EXPECT_EQ(Rational(3), r);
    EXPECT_FALSE(Rational::parse("1/0", r));
    EXPECT_FALSE(Rational::parse("1/2/3", r));
    EXPECT_FALSE(Rational::parse("x", r));
    EXPECT_FALSE(Rational::parse("", r));
    EXPECT_FALSE(Rational::parse("2/", r));
    EXPECT_EQ(Rational(3), r);
}
