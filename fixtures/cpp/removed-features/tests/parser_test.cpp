#include <gtest/gtest.h>

#include "toolkit/parser.h"

namespace {

using toolkit::ConfigParser;
using toolkit::ParseError;

TEST(ParserTest, ParsesKeyValues) {
    ConfigParser parser;
    parser.parse("# settings\nname = demo\n\nport= 8080\n  depth =3  \n");
    EXPECT_EQ(3, parser.size());
    EXPECT_EQ("demo", parser.get("name"));
    EXPECT_EQ(8080, parser.getInt("port"));
    EXPECT_EQ(3, parser.getInt("depth"));
    EXPECT_TRUE(parser.has("name"));
    EXPECT_FALSE(parser.has("missing"));
}

TEST(ParserTest, ReportsTheFailingLine) {
    ConfigParser parser;
    try {
        parser.parse("a = 1\nbroken line\n");
        FAIL() << "expected a ParseError";
    } catch (const ParseError& error) {
        EXPECT_EQ(2, error.line());
        EXPECT_STREQ("expected key = value", error.what());
    }
    EXPECT_THROW(parser.parse(" = value"), ParseError);
}

TEST(ParserTest, LookupErrors) {
    ConfigParser parser;
    parser.parse("word = abc");
    EXPECT_THROW(parser.get("nope"), std::out_of_range);
    EXPECT_THROW(parser.getInt("word"), std::invalid_argument);
}

}  // namespace
