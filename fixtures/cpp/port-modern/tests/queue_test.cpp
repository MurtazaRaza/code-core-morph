#include <gtest/gtest.h>

#include <memory>
#include <string>
#include <vector>

#include "jobs/queue.h"

namespace {

std::unique_ptr<jobs::Job> job(const std::string& name, int priority)
{
    auto j = std::make_unique<jobs::Job>();
    j->name = name;
    j->priority = priority;
    return j;
}

} // namespace

TEST(QueueTest, HighestPriorityFirstAndStableAmongEquals)
{
    jobs::JobQueue queue;
    queue.push(job("a", 1));
    queue.push(job("b", 3));
    queue.push(job("c", 1));
    queue.push(job("d", 3));
    EXPECT_EQ(4u, queue.size());
    EXPECT_EQ((std::vector<std::string>{"b", "d", "a", "c"}), queue.names());
    EXPECT_EQ("b", queue.pop()->name);
    EXPECT_EQ("d", queue.pop()->name);
    EXPECT_EQ("a", queue.pop()->name);
    EXPECT_EQ("c", queue.pop()->name);
    EXPECT_TRUE(queue.empty());
    EXPECT_EQ(nullptr, queue.pop());
}

TEST(QueueTest, TheQueueOwnsItsJobs)
{
    jobs::JobQueue queue;
    auto j = job("only", 0);
    j->id = 7;
    queue.push(std::move(j));
    EXPECT_EQ(nullptr, j);
    queue.push(nullptr);
    EXPECT_EQ(1u, queue.size());
    auto back = queue.pop();
    ASSERT_NE(nullptr, back);
    EXPECT_EQ(7, back->id);
}
