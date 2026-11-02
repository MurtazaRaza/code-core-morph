#include <gtest/gtest.h>

#include <sstream>

#include "ledger/parse.h"

TEST(ParseTest, ReadFieldSplitsOnCommas)
{
    std::istringstream in("a,bb,,c");
    std::string field;
    ASSERT_TRUE(ledger::read_field(in, field));
    EXPECT_EQ("a", field);
    ASSERT_TRUE(ledger::read_field(in, field));
    EXPECT_EQ("bb", field);
    ASSERT_TRUE(ledger::read_field(in, field));
    EXPECT_EQ("", field);
    ASSERT_TRUE(ledger::read_field(in, field));
    EXPECT_EQ("c", field);
    EXPECT_FALSE(ledger::read_field(in, field));
}

TEST(ParseTest, ParseAmount)
{
    ledger::Money m = { 7 };
    ASSERT_TRUE(ledger::parse_amount("12.50", m));
    EXPECT_EQ(1250, m.cents);
    EXPECT_FALSE(ledger::parse_amount("12.50x", m));
    EXPECT_FALSE(ledger::parse_amount("", m));
    EXPECT_FALSE(ledger::parse_amount("abc", m));
    EXPECT_EQ(1250, m.cents);
}

TEST(ParseTest, ParseCount)
{
    long n = 0;
    ASSERT_TRUE(ledger::parse_count("42", n));
    EXPECT_EQ(42, n);
    ASSERT_TRUE(ledger::parse_count("-3", n));
    EXPECT_EQ(-3, n);
    EXPECT_FALSE(ledger::parse_count("4.5", n));
    EXPECT_FALSE(ledger::parse_count("", n));
}

TEST(ParseTest, ParseRecords)
{
    std::istringstream in("bolt,10,0.25\n\nnut,200,0.05\n");
    std::vector<ledger::Record> records;
    int bad_line = -1;
    EXPECT_EQ(2u, ledger::parse_records(in, records, &bad_line));
    EXPECT_EQ(0, bad_line);
    ASSERT_EQ(2u, records.size());
    EXPECT_EQ("bolt", records[0].sku);
    EXPECT_EQ(10, records[0].quantity);
    EXPECT_EQ(25, records[0].unit_price.cents);
    EXPECT_EQ("nut", records[1].sku);
    EXPECT_EQ(5, records[1].unit_price.cents);
}

TEST(ParseTest, ParseRecordsStopsAtTheFirstBadLine)
{
    std::istringstream in("bolt,10,0.25\nwasher,many,0.01\nnut,200,0.05\n");
    std::vector<ledger::Record> records;
    int bad_line = 0;
    EXPECT_EQ(1u, ledger::parse_records(in, records, &bad_line));
    EXPECT_EQ(2, bad_line);
    EXPECT_EQ(1u, records.size());
}

TEST(ParseTest, ParseRecordsAcceptsNoBadLinePointer)
{
    std::istringstream in(",1,1\n");
    std::vector<ledger::Record> records;
    EXPECT_EQ(0u, ledger::parse_records(in, records, NULL));
}
