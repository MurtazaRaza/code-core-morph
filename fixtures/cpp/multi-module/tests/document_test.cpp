#include <gtest/gtest.h>

#include "mm/core/document.h"

namespace {

using mm::core::Document;

TEST(DocumentTest, CountsLines) {
    EXPECT_EQ(0, Document("t", "").lineCount());
    EXPECT_EQ(1, Document("t", "one line\n").lineCount());
    EXPECT_EQ(3, Document("t", "a\nb\nc").lineCount());
}

TEST(DocumentTest, LowerCasedWords) {
    Document doc("t", "The cat, the HAT; 3 dogs.");
    mm::util::StringList words = doc.words();
    ASSERT_EQ(5u, words.size());
    EXPECT_EQ("the", words[0]);
    EXPECT_EQ("hat", words[3]);
    EXPECT_EQ("dogs", words[4]);
    EXPECT_EQ(9u, doc.tokens().size());
}

}  // namespace
