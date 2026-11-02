#include <gtest/gtest.h>

#include "inventory/order.h"
#include "inventory/warehouse.h"

namespace {

using inventory::Customer;
using inventory::Item;
using inventory::Order;
using inventory::Warehouse;

TEST(OrderTest, GrowsAndTotals) {
    Customer alice = {"alice", 0.1};
    Order order(&alice);
    for (int i = 0; i < 5; ++i) {
        order.addLine("nut", 10, 0.5);
    }
    EXPECT_EQ(5, order.lineCount());
    EXPECT_DOUBLE_EQ(22.5, order.total());
    EXPECT_EQ(&alice, order.customer());
}

TEST(OrderTest, WithoutCustomer) {
    Order order(NULL);
    order.addLine("bolt", 2, 3.0);
    EXPECT_DOUBLE_EQ(6.0, order.total());
}

TEST(OrderTest, CopiesAreIndependent) {
    Order a(NULL);
    a.addLine("nut", 1, 1.0);
    Order b(a);
    b.addLine("bolt", 1, 2.0);
    EXPECT_EQ(1, a.lineCount());
    EXPECT_EQ(2, b.lineCount());
    Order c(NULL);
    c = b;
    EXPECT_EQ(2, c.lineCount());
    EXPECT_EQ("bolt", c.line(1).item);
}

TEST(OrderTest, FulfilsFromWarehouse) {
    Warehouse w("main");
    w.add(new Item("nut", 0.5, 10));
    w.add(new Item("bolt", 1.0, 3));
    Order ok(NULL);
    ok.addLine("nut", 4, 0.5);
    ok.addLine("bolt", 3, 1.0);
    EXPECT_TRUE(ok.fulfil(w));
    EXPECT_EQ(6, w.find("nut")->quantity());
    EXPECT_EQ(0, w.find("bolt")->quantity());

    Order tooMuch(NULL);
    tooMuch.addLine("nut", 1, 0.5);
    tooMuch.addLine("bolt", 1, 1.0);
    EXPECT_FALSE(tooMuch.fulfil(w));
    EXPECT_EQ(6, w.find("nut")->quantity());
}

}  // namespace
