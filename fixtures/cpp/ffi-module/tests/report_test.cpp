#include <gtest/gtest.h>

#include "textkit/report.h"

namespace {

TEST(ReportTest, DescribesARing) {
    tk_ring* ring = tk_ring_new(4);
    EXPECT_EQ(0u, textkit::fill(ring, {3, 4}));
    EXPECT_EQ("len=2 cap=4 sum=7", textkit::describe(ring));
    tk_ring_free(ring);
    EXPECT_EQ("(none)", textkit::describe(nullptr));
}

TEST(ReportTest, CountsWhatDoesNotFit) {
    tk_ring* ring = tk_ring_new(2);
    EXPECT_EQ(3u, textkit::fill(ring, {1, 2, 3, 4, 5}));
    EXPECT_EQ("len=2 cap=2 sum=3", textkit::describe(ring));
    tk_ring_free(ring);
}

}  // namespace
