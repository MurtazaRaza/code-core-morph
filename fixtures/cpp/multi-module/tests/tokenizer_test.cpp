#include <gtest/gtest.h>

#include "mm/util/tokenizer.h"

namespace {

using namespace mm::util;

TEST(TokenizerTest, ClassifiesTokens) {
    Tokenizer tokenizer("It's 42.5 degrees!");
    TokenList tokens = tokenizer.all();
    ASSERT_EQ(4u, tokens.size());
    EXPECT_EQ(kWord, tokens[0].kind);
    EXPECT_EQ("It's", tokens[0].text);
    EXPECT_EQ(kNumber, tokens[1].kind);
    EXPECT_EQ("42.5", tokens[1].text);
    EXPECT_EQ(kPunctuation, tokens[3].kind);
}

TEST(TokenizerTest, TracksLines) {
    Tokenizer tokenizer("one\ntwo\n\nthree");
    TokenList tokens = tokenizer.all();
    ASSERT_EQ(3u, tokens.size());
    EXPECT_EQ(1, tokens[0].line);
    EXPECT_EQ(2, tokens[1].line);
    EXPECT_EQ(4, tokens[2].line);
}

TEST(TokenizerTest, NextWithNullSkips) {
    Tokenizer tokenizer("a b");
    EXPECT_TRUE(tokenizer.next(NULL));
    Token token;
    EXPECT_TRUE(tokenizer.next(&token));
    EXPECT_EQ("b", token.text);
    EXPECT_FALSE(tokenizer.next(&token));
}

}  // namespace
