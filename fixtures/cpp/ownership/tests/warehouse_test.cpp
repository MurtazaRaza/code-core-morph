#include <gtest/gtest.h>

#include "inventory/warehouse.h"

namespace {

using inventory::Item;
using inventory::Perishable;
using inventory::Warehouse;

TEST(WarehouseTest, OwnsAndDeletesItems) {
    int before = Item::liveCount();
    {
        Warehouse w("main");
        w.add(new Item("nut", 0.1, 100));
        w.add(new Perishable("apple", 0.5, 20, 7));
        EXPECT_EQ(2, w.size());
        EXPECT_EQ(before + 2, Item::liveCount());
    }
    EXPECT_EQ(before, Item::liveCount());
}

TEST(WarehouseTest, MergesDuplicates) {
    Warehouse w("main");
    w.add(new Item("nut", 0.1, 100));
    w.add(new Item("nut", 0.1, 50));
    w.add(NULL);
    ASSERT_EQ(1, w.size());
    EXPECT_EQ(150, w.find("nut")->quantity());
}

TEST(WarehouseTest, RemoveReleaseAndFind) {
    Warehouse w("main");
    w.add(new Item("nut", 0.1, 100));
    w.add(new Item("bolt", 0.2, 10));
    EXPECT_TRUE(w.remove("nut"));
    EXPECT_FALSE(w.remove("nut"));
    EXPECT_TRUE(w.find("nut") == NULL);
    Item* bolt = w.release("bolt");
    ASSERT_TRUE(bolt != NULL);
    EXPECT_EQ(0, w.size());
    EXPECT_EQ("bolt", bolt->name());
    delete bolt;
    EXPECT_TRUE(w.release("bolt") == NULL);
}

TEST(WarehouseTest, CopiesAreDeep) {
    Warehouse a("a");
    a.add(new Item("nut", 0.1, 100));
    Warehouse b(a);
    b.find("nut")->setQuantity(1);
    EXPECT_EQ(100, a.find("nut")->quantity());
    Warehouse c("c");
    c = a;
    c = c;
    EXPECT_EQ("a", c.name());
    EXPECT_EQ(100, c.find("nut")->quantity());
}

TEST(WarehouseTest, ValueAndLowStock) {
    Warehouse w("main");
    w.add(new Item("nut", 0.5, 10));
    w.add(new Perishable("milk", 1.0, 2, 1));
    EXPECT_DOUBLE_EQ(6.0, w.totalValue());
    std::vector<std::string> low = w.lowStock(5);
    ASSERT_EQ(1u, low.size());
    EXPECT_EQ("milk", low[0]);
}

}  // namespace
