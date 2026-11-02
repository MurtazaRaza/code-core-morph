#include <gtest/gtest.h>

#include "inventory/factory.h"

namespace {

using inventory::Item;
using inventory::Report;

TEST(FactoryTest, CreatesItems) {
    Item* item = inventory::createItem("item:nut:0.5:10");
    ASSERT_TRUE(item != NULL);
    EXPECT_EQ("nut", item->name());
    EXPECT_DOUBLE_EQ(5.0, item->value());
    delete item;

    Item* milk = inventory::createItem("perishable:milk:1:4:2");
    ASSERT_TRUE(milk != NULL);
    EXPECT_DOUBLE_EQ(2.0, milk->value());
    delete milk;
}

TEST(FactoryTest, RejectsMalformedSpecs) {
    EXPECT_TRUE(inventory::createItem("") == NULL);
    EXPECT_TRUE(inventory::createItem("item:nut:abc:1") == NULL);
    EXPECT_TRUE(inventory::createItem("item::1:1") == NULL);
    EXPECT_TRUE(inventory::createItem("gadget:x:1:1") == NULL);
    EXPECT_TRUE(inventory::createItem("perishable:milk:1:4") == NULL);
}

TEST(FactoryTest, BatchCreateAndDestroy) {
    int before = Item::liveCount();
    std::vector<std::string> specs;
    specs.push_back("item:a:1:1");
    specs.push_back("broken");
    specs.push_back("item:b:2:2");
    std::vector<Item*> items = inventory::createItems(specs);
    EXPECT_EQ(2u, items.size());
    EXPECT_EQ(before + 2, Item::liveCount());
    inventory::destroyItems(items);
    EXPECT_TRUE(items.empty());
    EXPECT_EQ(before, Item::liveCount());
}

TEST(FactoryTest, Reports) {
    std::vector<std::string> specs;
    specs.push_back("item:a:1:1");
    specs.push_back("item:b:2:2");
    std::vector<Item*> items = inventory::createItems(specs);
    Report* report = inventory::makeReport("stock", items);
    ASSERT_EQ(3u, report->lines.size());
    EXPECT_EQ("stock", report->title);
    EXPECT_EQ("a x1 @ 1", report->lines[0]);
    EXPECT_EQ("total: 5", report->lines[2]);
    delete report;
    inventory::destroyItems(items);
}

}  // namespace
