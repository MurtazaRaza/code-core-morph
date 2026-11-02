#include <gtest/gtest.h>

#include <map>
#include <string>
#include <vector>

#include "meta/format.h"

namespace {

TEST(FormatTest, Scalars) {
    EXPECT_EQ("42", meta::toString(42));
    EXPECT_EQ("2.5", meta::toString(2.5));
    EXPECT_EQ("true", meta::toString(true));
    EXPECT_EQ("\"hi\"", meta::toString(std::string("hi")));
    EXPECT_EQ("\"a\\\"b\"", meta::toString("a\"b"));
}

TEST(FormatTest, PairsAndContainers) {
    std::vector<int> v;
    v.push_back(1);
    v.push_back(2);
    EXPECT_EQ("[1, 2]", meta::toString(v));
    EXPECT_EQ("(1, \"x\")", meta::toString(std::make_pair(1, std::string("x"))));
    std::map<std::string, int> m;
    m["b"] = 2;
    m["a"] = 1;
    EXPECT_EQ("[(\"a\", 1), (\"b\", 2)]", meta::toString(m));
    std::vector<std::vector<int> > nested(2, v);
    EXPECT_EQ("[[1, 2], [1, 2]]", meta::toString(nested));
    EXPECT_EQ("[]", meta::toString(std::vector<int>()));
}

TEST(FormatTest, Quantities) {
    meta::Quantity<int> q(3, "kg");
    EXPECT_EQ("3 kg", q.str());
    EXPECT_EQ(6, q.scaled(2).value());
    meta::Quantity<double> d(1.5, "l");
    EXPECT_EQ("3 l", d.scaled(2).str());
}

}  // namespace
