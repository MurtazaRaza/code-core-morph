#include <gtest/gtest.h>

#include <set>
#include <thread>
#include <vector>

#include "jobs/ids.h"

TEST(IdsTest, CountFromOne)
{
    jobs::reset_ids();
    EXPECT_EQ(1, jobs::next_id());
    EXPECT_EQ(2, jobs::next_id());
    jobs::reset_ids();
    EXPECT_EQ(1, jobs::next_id());
}

TEST(IdsTest, EveryThreadGetsItsOwn)
{
    jobs::reset_ids();
    std::vector<std::vector<int>> seen(4);
    std::vector<std::thread> threads;
    for (auto& ids : seen) {
        threads.emplace_back([&ids] {
            for (int i = 0; i < 100; ++i)
                ids.push_back(jobs::next_id());
        });
    }
    for (auto& t : threads)
        t.join();
    std::set<int> all;
    for (const auto& ids : seen)
        all.insert(ids.begin(), ids.end());
    EXPECT_EQ(400u, all.size());
    EXPECT_EQ(1, *all.begin());
    EXPECT_EQ(400, *all.rbegin());
}
