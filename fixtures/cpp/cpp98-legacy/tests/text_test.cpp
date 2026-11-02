#include <gtest/gtest.h>

#include "ledger/text.h"

using ledger::Utf16;

TEST(TextTest, AsciiRoundTrips)
{
    Utf16 utf16 = ledger::utf8_to_utf16("Bolt M8");
    ASSERT_EQ(7u, utf16.size());
    EXPECT_EQ('B', utf16[0]);
    EXPECT_EQ("Bolt M8", ledger::utf16_to_utf8(utf16));
}

TEST(TextTest, MultiByteAndSurrogatePairs)
{
    // "€" (U+20AC) and "𝄞" (U+1D11E, outside the basic plane).
    const std::string text = "\xE2\x82\xAC\xF0\x9D\x84\x9E";
    Utf16 utf16 = ledger::utf8_to_utf16(text);
    ASSERT_EQ(3u, utf16.size());
    EXPECT_EQ(0x20AC, utf16[0]);
    EXPECT_EQ(0xD834, utf16[1]);
    EXPECT_EQ(0xDD1E, utf16[2]);
    EXPECT_EQ(text, ledger::utf16_to_utf8(utf16));
}

TEST(TextTest, InvalidInputBecomesReplacementCharacters)
{
    Utf16 utf16 = ledger::utf8_to_utf16("a\xFF" "b");
    ASSERT_EQ(3u, utf16.size());
    EXPECT_EQ(0xFFFD, utf16[1]);

    Utf16 lone;
    lone.push_back(0xD800);
    EXPECT_EQ("\xEF\xBF\xBD", ledger::utf16_to_utf8(lone));
}

TEST(TextTest, EmptyInput)
{
    EXPECT_TRUE(ledger::utf8_to_utf16("").empty());
    EXPECT_EQ("", ledger::utf16_to_utf8(Utf16()));
}

TEST(TextTest, Utf8Length)
{
    EXPECT_EQ(0u, ledger::utf8_length(""));
    EXPECT_EQ(5u, ledger::utf8_length("bolts"));
    EXPECT_EQ(2u, ledger::utf8_length("\xE2\x82\xAC\xF0\x9D\x84\x9E"));
}
