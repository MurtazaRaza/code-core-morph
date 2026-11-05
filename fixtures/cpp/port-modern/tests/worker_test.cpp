#include <gtest/gtest.h>

#include <memory>
#include <mutex>
#include <vector>

#include "jobs/events.h"
#include "jobs/queue.h"
#include "jobs/worker.h"

namespace {

std::unique_ptr<jobs::Job> job(int id, int priority)
{
    auto j = std::make_unique<jobs::Job>();
    j->id = id;
    j->name = "job";
    j->priority = priority;
    return j;
}

} // namespace

TEST(WorkerTest, RunsTheQueueInOrderAndPublishesEachJob)
{
    jobs::EventBus bus;
    std::mutex mutex;
    std::vector<int> done;
    bus.subscribe("done", [&](const jobs::Event& e) {
        std::lock_guard<std::mutex> lock(mutex);
        done.push_back(e.job_id);
    });
    jobs::JobQueue queue;
    queue.push(job(1, 0));
    queue.push(job(2, 5));
    queue.push(job(3, 0));
    jobs::Worker worker(bus);
    EXPECT_EQ(3, worker.run(queue));
    EXPECT_TRUE(queue.empty());
    EXPECT_EQ((std::vector<int>{2, 1, 3}), done);
    EXPECT_EQ(3, worker.total());
}

TEST(WorkerTest, TotalsAddUp)
{
    jobs::EventBus bus;
    jobs::Worker worker(bus);
    jobs::JobQueue queue;
    EXPECT_EQ(0, worker.run(queue));
    queue.push(job(1, 0));
    EXPECT_EQ(1, worker.run(queue));
    queue.push(job(2, 0));
    queue.push(job(3, 0));
    EXPECT_EQ(2, worker.run(queue));
    EXPECT_EQ(3, worker.total());
}
