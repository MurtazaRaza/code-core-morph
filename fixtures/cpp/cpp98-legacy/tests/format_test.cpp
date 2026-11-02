#include <gtest/gtest.h>

#include "ledger/format.h"

TEST(FormatTest, FormatCount)
{
    EXPECT_EQ("1234567", ledger::format_count(1234567));
    EXPECT_EQ("-42", ledger::format_count(-42));
    EXPECT_EQ("9000000000", ledger::format_count(9000000000LL));
}

TEST(FormatTest, FormatId)
{
    EXPECT_EQ("INV-00000042", ledger::format_id("INV", 42));
    EXPECT_EQ("PO-12345678", ledger::format_id("PO", 12345678));
}

TEST(FormatTest, Upper)
{
    EXPECT_EQ("WIDGET-7", ledger::upper("widget-7"));
    EXPECT_EQ("", ledger::upper(""));
}

TEST(FormatTest, Join)
{
    ledger::StringList parts;
    EXPECT_EQ("", ledger::join(parts, ", "));
    parts.push_back("a");
    EXPECT_EQ("a", ledger::join(parts, ", "));
    parts.push_back("b");
    parts.push_back("c");
    EXPECT_EQ("a, b, c", ledger::join(parts, ", "));
}

TEST(FormatTest, PadLeft)
{
    EXPECT_EQ("   42", ledger::pad_left("42", 5));
    EXPECT_EQ("123456", ledger::pad_left("123456", 3));
}
