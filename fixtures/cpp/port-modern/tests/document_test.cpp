#include <gtest/gtest.h>

#include "jobs/document.h"

TEST(DocumentTest, CopiesShareTheirTextUntilOneChanges)
{
    jobs::Document a("hello world");
    jobs::Document b = a;
    EXPECT_TRUE(a.shares_text_with(b));
    b.append("!");
    EXPECT_FALSE(a.shares_text_with(b));
    EXPECT_EQ("hello world", a.text());
    EXPECT_EQ("hello world!", b.text());
    jobs::Document c = b;
    b.append("");
    EXPECT_TRUE(b.shares_text_with(c));
}

TEST(DocumentTest, Words)
{
    EXPECT_EQ(0u, jobs::Document().words());
    EXPECT_EQ(0u, jobs::Document(" \t\n").words());
    EXPECT_EQ(3u, jobs::Document("  one two\tthree\n").words());
    jobs::Document d("a");
    d.append(" b c");
    EXPECT_EQ(3u, d.words());
}
