#include <gtest/gtest.h>

#include "notes/note.h"

namespace {

using notes::Note;

TEST(NoteTest, KeepsItsFields) {
    Note note(7, "groceries", "milk, eggs");
    EXPECT_EQ(7u, note.id());
    EXPECT_EQ("groceries", note.title());
    EXPECT_EQ("milk, eggs", note.body());
    EXPECT_TRUE(note.tags().empty());
}

TEST(NoteTest, TagsAreUnique) {
    Note note(1, "t", "b");
    note.addTag("home");
    note.addTag("home");
    note.addTag("urgent");
    ASSERT_EQ(2u, note.tags().size());
    EXPECT_TRUE(note.hasTag("home"));
    EXPECT_TRUE(note.hasTag("urgent"));
    EXPECT_FALSE(note.hasTag("work"));
}

}  // namespace
