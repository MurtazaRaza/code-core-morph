#include <gtest/gtest.h>

#include "ledger/money.h"

using ledger::Money;

namespace {

Money cents(long value)
{
    Money m = { value };
    return m;
}

} // namespace

TEST(MoneyTest, FromUnitsRoundsHalfAwayFromZero)
{
    EXPECT_EQ(1235, ledger::money_from_units(12.345).cents);
    EXPECT_EQ(-1235, ledger::money_from_units(-12.345).cents);
    EXPECT_EQ(0, ledger::money_from_units(0.004).cents);
    EXPECT_EQ(100, ledger::money_from_units(1.0).cents);
}

TEST(MoneyTest, AddAndSubtract)
{
    EXPECT_EQ(cents(350), ledger::add(cents(100), cents(250)));
    EXPECT_EQ(cents(-150), ledger::subtract(cents(100), cents(250)));
}

TEST(MoneyTest, ScaleTruncatesTowardZero)
{
    EXPECT_EQ(1500, ledger::scale(cents(1000), 1.5).cents);
    EXPECT_EQ(499, ledger::scale(cents(999), 0.5).cents);
    EXPECT_EQ(-499, ledger::scale(cents(-999), 0.5).cents);
}

TEST(MoneyTest, SplitSpreadsTheRemainder)
{
    Money parts[3];
    ledger::split(cents(100), 3, parts);
    EXPECT_EQ(34, parts[0].cents);
    EXPECT_EQ(33, parts[1].cents);
    EXPECT_EQ(33, parts[2].cents);

    ledger::split(cents(-100), 3, parts);
    EXPECT_EQ(-34, parts[0].cents);
    EXPECT_EQ(-33, parts[2].cents);
}

TEST(MoneyTest, SplitIgnoresBadArguments)
{
    Money parts[1] = { { 7 } };
    ledger::split(cents(100), 0, parts);
    EXPECT_EQ(7, parts[0].cents);
    ledger::split(cents(100), 2, NULL);
}

TEST(MoneyTest, Comparisons)
{
    EXPECT_TRUE(cents(1) < cents(2));
    EXPECT_FALSE(cents(2) < cents(2));
    EXPECT_TRUE(cents(2) != cents(3));
}

TEST(MoneyTest, ToString)
{
    EXPECT_EQ("12.35", ledger::to_string(cents(1235)));
    EXPECT_EQ("-0.05", ledger::to_string(cents(-5)));
    EXPECT_EQ("0.00", ledger::to_string(cents(0)));
    EXPECT_EQ("1000.10", ledger::to_string(cents(100010)));
}
