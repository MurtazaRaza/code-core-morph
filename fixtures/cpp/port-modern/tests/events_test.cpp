#include <gtest/gtest.h>

#include <stdexcept>
#include <string>
#include <vector>

#include "jobs/events.h"

TEST(EventsTest, HandlersOfTheTopicAndOfEveryTopic)
{
    jobs::EventBus bus;
    std::vector<std::string> calls;
    bus.subscribe("done", [&](const jobs::Event& e) { calls.push_back("done " + std::to_string(e.job_id)); });
    bus.subscribe("*", [&](const jobs::Event& e) { calls.push_back("any " + e.topic); });
    bus.subscribe("failed", [&](const jobs::Event&) { calls.push_back("failed"); });
    EXPECT_EQ(2, bus.publish(jobs::Event{"done", 4}));
    EXPECT_EQ(1, bus.publish(jobs::Event{"started", 5}));
    EXPECT_EQ((std::vector<std::string>{"done 4", "any done", "any started"}), calls);
    EXPECT_EQ(3u, bus.subscriptions());
}

TEST(EventsTest, Unsubscribe)
{
    jobs::EventBus bus;
    int count = 0;
    int first = bus.subscribe("x", [&](const jobs::Event&) { ++count; });
    int second = bus.subscribe("x", [&](const jobs::Event&) { count += 10; });
    EXPECT_NE(first, second);
    EXPECT_TRUE(bus.unsubscribe(first));
    EXPECT_FALSE(bus.unsubscribe(first));
    EXPECT_FALSE(bus.unsubscribe(99));
    EXPECT_EQ(1, bus.publish(jobs::Event{"x", 0}));
    EXPECT_EQ(10, count);
}

TEST(EventsTest, AnEmptyHandlerIsRefused)
{
    jobs::EventBus bus;
    EXPECT_THROW(bus.subscribe("x", jobs::EventBus::Handler()), std::invalid_argument);
    EXPECT_EQ(0u, bus.subscriptions());
}
