#include <gtest/gtest.h>

#include "toolkit/logger.h"

namespace {

using toolkit::Logger;
using toolkit::MemorySink;
using toolkit::Sink;

TEST(LoggerTest, WritesToItsSink) {
    MemorySink* sink = 0;
    std::auto_ptr<Logger> logger = toolkit::makeMemoryLogger(&sink);
    logger->info("started");
    logger->error("failed");
    ASSERT_EQ(2u, sink->lines.size());
    EXPECT_EQ("info: started", sink->lines[0]);
    EXPECT_EQ("error: failed", sink->lines[1]);
    EXPECT_EQ(2, logger->count());
}

TEST(LoggerTest, DetachHandsTheSinkBack) {
    MemorySink* sink = 0;
    std::auto_ptr<Logger> logger = toolkit::makeMemoryLogger(&sink);
    std::auto_ptr<Sink> detached = logger->detach();
    EXPECT_FALSE(logger->hasSink());
    logger->info("dropped");
    EXPECT_EQ(1, logger->count());
    EXPECT_TRUE(sink->lines.empty());
    EXPECT_EQ(sink, detached.get());
}

TEST(LoggerTest, AutoPtrCopiesTransferOwnership) {
    std::auto_ptr<Logger> first = toolkit::makeMemoryLogger(0);
    std::auto_ptr<Logger> second = first;
    EXPECT_TRUE(first.get() == 0);
    EXPECT_TRUE(second.get() != 0);
}

}  // namespace
