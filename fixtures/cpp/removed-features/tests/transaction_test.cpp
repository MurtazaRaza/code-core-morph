#include <gtest/gtest.h>

#include <stdexcept>

#include "toolkit/transaction.h"

namespace {

using toolkit::Journal;
using toolkit::Transaction;

TEST(TransactionTest, CommitKeepsEntries) {
    Journal journal;
    {
        Transaction tx(journal);
        tx.record("a");
        tx.record("b");
        tx.commit();
    }
    EXPECT_EQ(2u, journal.entries.size());
}

TEST(TransactionTest, NoCommitRollsBack) {
    Journal journal;
    journal.append("kept");
    {
        Transaction tx(journal);
        tx.record("dropped");
    }
    ASSERT_EQ(1u, journal.entries.size());
    EXPECT_EQ("kept", journal.entries[0]);
}

TEST(TransactionTest, ExceptionRollsBackEvenAfterCommit) {
    Journal journal;
    try {
        Transaction tx(journal);
        tx.record("x");
        tx.commit();
        throw std::runtime_error("boom");
    } catch (const std::runtime_error&) {
    }
    EXPECT_TRUE(journal.entries.empty());
}

}  // namespace
