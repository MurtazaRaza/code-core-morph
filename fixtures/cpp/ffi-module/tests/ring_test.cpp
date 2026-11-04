#include <gtest/gtest.h>

#include <cstring>

#include "textkit/ring.h"

namespace {

TEST(RingTest, ZeroCapacityIsRefused) {
    EXPECT_EQ(nullptr, tk_ring_new(0));
    tk_ring_free(nullptr);  // allowed
}

TEST(RingTest, PopsInTheOrderPushed) {
    tk_ring* ring = tk_ring_new(3);
    ASSERT_NE(nullptr, ring);
    EXPECT_EQ(1, tk_ring_push(ring, 10));
    EXPECT_EQ(1, tk_ring_push(ring, 20));
    int32_t value = 0;
    EXPECT_EQ(1, tk_ring_pop(ring, &value));
    EXPECT_EQ(10, value);
    EXPECT_EQ(1, tk_ring_push(ring, 30));
    EXPECT_EQ(1, tk_ring_push(ring, 40));  // wraps around the end
    EXPECT_EQ(3u, tk_ring_len(ring));
    EXPECT_EQ(1, tk_ring_pop(ring, &value));
    EXPECT_EQ(20, value);
    EXPECT_EQ(1, tk_ring_pop(ring, &value));
    EXPECT_EQ(30, value);
    EXPECT_EQ(1, tk_ring_pop(ring, &value));
    EXPECT_EQ(40, value);
    EXPECT_EQ(0, tk_ring_pop(ring, &value));
    tk_ring_free(ring);
}

TEST(RingTest, RefusesValuesWhenFull) {
    tk_ring* ring = tk_ring_new(2);
    EXPECT_EQ(1, tk_ring_push(ring, 1));
    EXPECT_EQ(1, tk_ring_push(ring, 2));
    EXPECT_EQ(0, tk_ring_push(ring, 3));
    EXPECT_EQ(2u, tk_ring_len(ring));
    EXPECT_EQ(2u, tk_ring_capacity(ring));
    tk_ring_free(ring);
}

TEST(RingTest, SumsWithoutOverflow) {
    tk_ring* ring = tk_ring_new(4);
    tk_ring_push(ring, INT32_MAX);
    tk_ring_push(ring, INT32_MAX);
    tk_ring_push(ring, -5);
    EXPECT_EQ(static_cast<int64_t>(INT32_MAX) * 2 - 5, tk_ring_sum(ring));
    tk_ring_free(ring);
}

TEST(ChecksumTest, MatchesKnownAdler32Values) {
    const char* text = "Wikipedia";
    EXPECT_EQ(0x11E60398u,
              tk_checksum(reinterpret_cast<const uint8_t*>(text), std::strlen(text)));
    EXPECT_EQ(1u, tk_checksum(nullptr, 0));
}

TEST(ChecksumTest, WrapsAroundTheModulus) {
    uint8_t data[6000];
    std::memset(data, 0xFF, sizeof data);
    EXPECT_EQ(0xA49759EAu, tk_checksum(data, sizeof data));  // zlib.adler32
}

}  // namespace
