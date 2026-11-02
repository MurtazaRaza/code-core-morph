#include <gtest/gtest.h>

#include "ccore/buffer.hpp"
#include "ccore/table.hpp"

namespace {

TEST(BufferTest, ChainsAndCopies) {
    ccore::Buffer a;
    a.append("abc").append("def");
    ccore::Buffer b(a);
    b.erase(0, 3);
    EXPECT_EQ("abcdef", a.str());
    EXPECT_EQ("def", b.str());
    ccore::Buffer c;
    c = a;
    EXPECT_EQ(a.hash(), c.hash());
    EXPECT_NE(a.hash(), b.hash());
    c.clear();
    EXPECT_EQ(0u, c.size());
    EXPECT_EQ("", c.str());
}

TEST(BufferTest, EraseOutOfRangeThrows) {
    ccore::Buffer a;
    a.append("xy");
    EXPECT_THROW(a.erase(1, 5), std::out_of_range);
}

TEST(StringListTest, WrapsTheCList) {
    ccore::StringList list;
    list.push("a");
    list.push("b");
    list.push("c");
    EXPECT_TRUE(list.remove("b"));
    EXPECT_FALSE(list.remove("zz"));
    ccore::Strings items = list.items();
    ASSERT_EQ(2u, items.size());
    EXPECT_EQ("c", items[1]);
    EXPECT_EQ("a, c", list.join(", "));
}

TEST(InternTableTest, AssignsStableIds) {
    ccore::InternTable table(8);
    int a = table.intern("alpha");
    int b = table.intern("beta");
    EXPECT_EQ(a, table.intern("alpha"));
    EXPECT_NE(a, b);
    EXPECT_EQ(2, table.count());
    EXPECT_TRUE(table.contains("beta"));
    EXPECT_FALSE(table.contains("gamma"));
}

}  // namespace
