#include <gtest/gtest.h>

#include "mm/core/counter.h"

namespace {

using mm::core::Document;
using mm::core::Ranking;
using mm::core::WordCounter;

TEST(WordCounterTest, CountsWords) {
    WordCounter counter;
    counter.add(Document("a", "the cat and the hat"));
    counter.add(Document("b", "The end"));
    EXPECT_EQ(3, counter.count("the"));
    EXPECT_EQ(0, counter.count("dog"));
    EXPECT_EQ(7, counter.total());
    EXPECT_EQ(5, counter.distinct());
    counter.addWord("");
    EXPECT_EQ(7, counter.total());
}

TEST(WordCounterTest, RanksByCountThenName) {
    WordCounter counter;
    counter.add(Document("a", "b a c a b a"));
    Ranking top = counter.top(2);
    ASSERT_EQ(2u, top.size());
    EXPECT_EQ("a", top[0].first);
    EXPECT_EQ(3, top[0].second);
    EXPECT_EQ("b", top[1].first);
    EXPECT_EQ(3u, counter.top(-1).size());
    EXPECT_EQ(3u, counter.top(10).size());
}

}  // namespace
