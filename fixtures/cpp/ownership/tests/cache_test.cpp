#include <gtest/gtest.h>

#include "inventory/cache.h"

namespace {

using inventory::PriceCache;

TEST(PriceCacheTest, StoresAndEvictsLeastRecent) {
    PriceCache cache(2);
    cache.put("a", 1.0);
    cache.put("b", 2.0);
    double price = 0.0;
    EXPECT_TRUE(cache.get("a", &price));
    EXPECT_DOUBLE_EQ(1.0, price);
    cache.put("c", 3.0);  // evicts b, the least recently used
    EXPECT_EQ(2, cache.size());
    EXPECT_FALSE(cache.get("b", &price));
    EXPECT_TRUE(cache.get("c", NULL));
    EXPECT_EQ("a", cache.oldest());
}

TEST(PriceCacheTest, UpdatesExistingKeys) {
    PriceCache cache(2);
    cache.put("a", 1.0);
    cache.put("a", 5.0);
    double price = 0.0;
    EXPECT_EQ(1, cache.size());
    EXPECT_TRUE(cache.get("a", &price));
    EXPECT_DOUBLE_EQ(5.0, price);
}

TEST(PriceCacheTest, CapacityIsAtLeastOne) {
    PriceCache cache(0);
    cache.put("a", 1.0);
    cache.put("b", 2.0);
    EXPECT_EQ(1, cache.size());
    EXPECT_EQ("b", cache.oldest());
    PriceCache empty(3);
    EXPECT_EQ("", empty.oldest());
}

}  // namespace
