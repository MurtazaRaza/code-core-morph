#include <gtest/gtest.h>

#include "calc/lexer.h"

using calc::Lexer;
using calc::Token;

TEST(LexerTest, SplitsAnExpression)
{
    Lexer lexer("12 + (ans*3)");
    Token t = lexer.next();
    EXPECT_EQ(Token::NUMBER, t.kind);
    EXPECT_EQ("12", t.text);
    EXPECT_EQ(0u, t.pos);
    t = lexer.next();
    EXPECT_EQ(Token::OPERATOR, t.kind);
    EXPECT_EQ("+", t.text);
    EXPECT_EQ(3u, t.pos);
    t = lexer.next();
    EXPECT_EQ(Token::LPAREN, t.kind);
    EXPECT_EQ(5u, t.pos);
    t = lexer.next();
    EXPECT_EQ(Token::NAME, t.kind);
    EXPECT_EQ("ans", t.text);
    EXPECT_EQ(6u, t.pos);
    t = lexer.next();
    EXPECT_EQ(Token::OPERATOR, t.kind);
    EXPECT_EQ("*", t.text);
    t = lexer.next();
    EXPECT_EQ(Token::NUMBER, t.kind);
    EXPECT_EQ("3", t.text);
    t = lexer.next();
    EXPECT_EQ(Token::RPAREN, t.kind);
    EXPECT_EQ(11u, t.pos);
    t = lexer.next();
    EXPECT_EQ(Token::END, t.kind);
    EXPECT_EQ(12u, t.pos);
    EXPECT_EQ(Token::END, lexer.next().kind);
}

TEST(LexerTest, NamesAreLowercased)
{
    Lexer lexer("ANS My_Var");
    Token t = lexer.next();
    EXPECT_EQ(Token::NAME, t.kind);
    EXPECT_EQ("ans", t.text);
    t = lexer.next();
    EXPECT_EQ("my_var", t.text);
    EXPECT_EQ(4u, t.pos);
}

TEST(LexerTest, PeekDoesNotConsume)
{
    Lexer lexer("  7");
    EXPECT_EQ("7", lexer.peek().text);
    EXPECT_EQ(0u, lexer.position());
    EXPECT_EQ("7", lexer.next().text);
    EXPECT_EQ(3u, lexer.position());
}

TEST(LexerTest, OtherCharactersAreInvalidTokens)
{
    Lexer lexer("1 $ 2");
    lexer.next();
    Token bad = lexer.next();
    EXPECT_EQ(Token::INVALID, bad.kind);
    EXPECT_EQ("$", bad.text);
    EXPECT_EQ(2u, bad.pos);
    EXPECT_EQ("2", lexer.next().text);
}
