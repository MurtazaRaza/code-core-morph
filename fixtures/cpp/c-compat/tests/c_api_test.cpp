#include <gtest/gtest.h>

#include <cstdlib>
#include <cstring>
#include <string>

#include "ccore/buffer.h"
#include "ccore/hash.h"
#include "ccore/strlist.h"

namespace {

TEST(CBufferTest, AppendsAndErases) {
    ccore_buffer buffer;
    ccore_buffer_init(&buffer);
    EXPECT_EQ(CCORE_OK, ccore_buffer_append_str(&buffer, "hello"));
    EXPECT_EQ(CCORE_OK, ccore_buffer_append_str(&buffer, " world, longer than sixteen"));
    EXPECT_EQ(32u, buffer.size);
    EXPECT_EQ(CCORE_OK, ccore_buffer_erase(&buffer, 5, 6));
    EXPECT_EQ(std::string("hello, longer than sixteen"),
              std::string(reinterpret_cast<char*>(buffer.data), buffer.size));
    EXPECT_EQ(CCORE_OUT_OF_RANGE, ccore_buffer_erase(&buffer, 20, 100));
    ccore_buffer_free(&buffer);
    EXPECT_TRUE(buffer.data == NULL);
}

TEST(CHashTest, Fnv1aKnownValues) {
    EXPECT_EQ(2166136261u, ccore_fnv1a_str(""));
    EXPECT_EQ(0xe40c292cu, ccore_fnv1a_str("a"));
    EXPECT_EQ(0xbf9cf968u, ccore_fnv1a_str("foobar"));
}

TEST(CHashTest, SetAddsOnce) {
    ccore_set set;
    ASSERT_TRUE(ccore_set_init(&set, 4));
    EXPECT_EQ(1, ccore_set_add(&set, "x"));
    EXPECT_EQ(0, ccore_set_add(&set, "x"));
    EXPECT_EQ(1, ccore_set_add(&set, "y"));
    EXPECT_TRUE(ccore_set_contains(&set, "y"));
    EXPECT_FALSE(ccore_set_contains(&set, "z"));
    EXPECT_EQ(2u, set.count);
    ccore_set_free(&set);
}

void countVisit(const char* text, void* context) {
    *static_cast<size_t*>(context) += std::strlen(text);
}

TEST(CListTest, PushRemoveVisitJoin) {
    ccore_list list;
    ccore_list_init(&list);
    ccore_list_push(&list, "one");
    ccore_list_push(&list, "two");
    ccore_list_push(&list, "three");
    EXPECT_EQ(1, ccore_list_remove(&list, "two"));
    EXPECT_EQ(0, ccore_list_remove(&list, "two"));
    EXPECT_EQ(2u, list.length);
    size_t letters = 0;
    ccore_list_visit(&list, countVisit, &letters);
    EXPECT_EQ(8u, letters);
    char* joined = ccore_list_join(&list, "+");
    EXPECT_STREQ("one+three", joined);
    std::free(joined);
    ccore_list_free(&list);
    EXPECT_EQ(0u, list.length);
}

}  // namespace
