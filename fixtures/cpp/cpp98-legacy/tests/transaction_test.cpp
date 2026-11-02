#include <gtest/gtest.h>

#include "ledger/transaction.h"

using ledger::Inventory;
using ledger::Transaction;

TEST(TransactionTest, CommitAppliesEveryChange)
{
    Inventory inventory;
    inventory.add("nut", 5);
    Transaction transaction(inventory, true);
    transaction.add("bolt", 10);
    transaction.remove("nut", 2);
    EXPECT_EQ(2u, transaction.pending());
    EXPECT_EQ(0, inventory.count("bolt"));
    transaction.commit();
    EXPECT_TRUE(transaction.committed());
    EXPECT_EQ(0u, transaction.pending());
    EXPECT_EQ(10, inventory.count("bolt"));
    EXPECT_EQ(3, inventory.count("nut"));
}

TEST(TransactionTest, FailedCommitRollsBack)
{
    Inventory inventory;
    inventory.add("nut", 1);
    Transaction transaction(inventory, false);
    transaction.add("bolt", 10);
    transaction.remove("nut", 1);
    transaction.remove("nut", 1);
    EXPECT_THROW(transaction.commit(), std::out_of_range);
    EXPECT_FALSE(transaction.committed());
    EXPECT_EQ(0, inventory.count("bolt"));
    EXPECT_EQ(1, inventory.count("nut"));
}

TEST(TransactionTest, StrictTransactionWithoutCommitThrowsFromItsDestructor)
{
    Inventory inventory;
    EXPECT_THROW(
        {
            Transaction transaction(inventory, true);
            transaction.add("bolt", 1);
        },
        ledger::RollbackError);
    EXPECT_EQ(0, inventory.count("bolt"));
}

TEST(TransactionTest, LenientOrEmptyTransactionsDontThrow)
{
    Inventory inventory;
    EXPECT_NO_THROW({
        Transaction lenient(inventory, false);
        lenient.add("bolt", 1);
    });
    EXPECT_NO_THROW({ Transaction empty(inventory, true); });
}

TEST(TransactionTest, NoSecondExceptionWhileUnwinding)
{
    Inventory inventory;
    EXPECT_THROW(
        {
            Transaction transaction(inventory, true);
            transaction.add("bolt", 1);
            throw std::logic_error("caller failed");
        },
        std::logic_error);
}
