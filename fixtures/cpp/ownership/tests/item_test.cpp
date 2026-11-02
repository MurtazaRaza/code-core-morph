#include <gtest/gtest.h>

#include "inventory/item.h"

namespace {

using inventory::Item;
using inventory::Perishable;

TEST(ItemTest, ValueAndDescription) {
    Item item("bolt", 0.25, 40);
    EXPECT_DOUBLE_EQ(10.0, item.value());
    EXPECT_EQ("bolt x40 @ 0.25", item.describe());
}

TEST(ItemTest, PerishableLosesValue) {
    Perishable fresh("milk", 1.0, 10, 5);
    Perishable soon("bread", 2.0, 5, 2);
    Perishable spoiled("fish", 9.0, 3, 0);
    EXPECT_DOUBLE_EQ(10.0, fresh.value());
    EXPECT_DOUBLE_EQ(5.0, soon.value());
    EXPECT_DOUBLE_EQ(0.0, spoiled.value());
    EXPECT_EQ("bread x5 @ 2 (2 days left)", soon.describe());
}

TEST(ItemTest, CloneKeepsTheDynamicType) {
    int before = Item::liveCount();
    Item* original = new Perishable("cheese", 4.0, 2, 1);
    Item* copy = original->clone();
    EXPECT_EQ(before + 2, Item::liveCount());
    EXPECT_EQ(original->describe(), copy->describe());
    EXPECT_DOUBLE_EQ(4.0, copy->value());
    delete original;
    delete copy;
    EXPECT_EQ(before, Item::liveCount());
}

}  // namespace
