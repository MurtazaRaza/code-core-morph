#include <gtest/gtest.h>

#include "ledger/inventory.h"

using ledger::Inventory;

TEST(InventoryTest, AddRemoveAndCount)
{
    Inventory inventory;
    inventory.add("bolt", 10);
    inventory.add("bolt", 5);
    inventory.remove("bolt", 3);
    EXPECT_EQ(12, inventory.count("bolt"));
    EXPECT_EQ(0, inventory.count("nut"));
    EXPECT_EQ(1u, inventory.size());
}

TEST(InventoryTest, RemovingTooMuchThrows)
{
    Inventory inventory;
    inventory.add("bolt", 2);
    EXPECT_THROW(inventory.remove("bolt", 3), std::out_of_range);
    EXPECT_THROW(inventory.remove("nut", 1), std::out_of_range);
    EXPECT_EQ(2, inventory.count("bolt"));
}

TEST(InventoryTest, Find)
{
    Inventory inventory;
    EXPECT_TRUE(inventory.find("bolt") == NULL);
    inventory.add("bolt", 4);
    const int* found = inventory.find("bolt");
    ASSERT_TRUE(found != NULL);
    EXPECT_EQ(4, *found);
}

TEST(InventoryTest, SortedByCount)
{
    Inventory inventory;
    inventory.add("washer", 5);
    inventory.add("bolt", 10);
    inventory.add("nut", 5);
    ledger::StockLines lines = inventory.sorted_by_count();
    ASSERT_EQ(3u, lines.size());
    EXPECT_EQ("bolt", lines[0].first);
    EXPECT_EQ("nut", lines[1].first);
    EXPECT_EQ("washer", lines[2].first);
    EXPECT_EQ(5, lines[2].second);
}

TEST(InventoryTest, Below)
{
    Inventory inventory;
    inventory.add("bolt", 10);
    inventory.add("nut", 2);
    inventory.add("washer", 0);
    std::vector<std::string> low = inventory.below(3);
    ASSERT_EQ(2u, low.size());
    EXPECT_EQ("nut", low[0]);
    EXPECT_EQ("washer", low[1]);
    EXPECT_TRUE(inventory.below(0).empty());
}

TEST(InventoryTest, TotalAndClone)
{
    Inventory inventory;
    inventory.add("bolt", 10);
    inventory.add("nut", 2);
    EXPECT_EQ(12, inventory.total());
    std::auto_ptr<Inventory> copy = inventory.clone();
    inventory.add("bolt", 1);
    EXPECT_EQ(10, copy->count("bolt"));
    EXPECT_EQ(12, copy->total());
}

TEST(InventoryTest, AuditedInventoryCountsChanges)
{
    ledger::AuditedInventory inventory;
    inventory.add("bolt", 3);
    inventory.add("bolt", 0);
    inventory.remove("bolt", 1);
    EXPECT_EQ(2, inventory.changes());
}
