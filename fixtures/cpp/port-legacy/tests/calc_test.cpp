#include <gtest/gtest.h>

#include "calc/calc.h"

TEST(CalcApiTest, EvaluatesAndKeepsHistory)
{
    calc_session* s = calc_open();
    char out[32];
    EXPECT_EQ(CALC_OK, calc_eval(s, "1/2 + 1/3", out, sizeof out, NULL));
    EXPECT_STREQ("5/6", out);
    EXPECT_EQ(CALC_OK, calc_eval(s, "ans * 6", out, sizeof out, NULL));
    EXPECT_STREQ("5", out);
    EXPECT_EQ(2u, calc_history_size(s));
    EXPECT_EQ(CALC_OK, calc_undo(s));
    EXPECT_EQ(CALC_OK, calc_eval(s, "ans", out, sizeof out, NULL));
    EXPECT_STREQ("5/6", out);
    calc_close(s);
}

TEST(CalcApiTest, FailuresKeepNothing)
{
    calc_session* s = calc_open();
    char out[8];
    size_t pos = 99;
    EXPECT_EQ(CALC_SYNTAX_ERROR, calc_eval(s, "2 * (3", out, sizeof out, &pos));
    EXPECT_EQ(6u, pos);
    EXPECT_EQ(CALC_DIVISION_BY_ZERO, calc_eval(s, "1/0", out, sizeof out, &pos));
    EXPECT_EQ(CALC_BUFFER_TOO_SMALL, calc_eval(s, "1/123456789", out, sizeof out, NULL));
    EXPECT_EQ(0u, calc_history_size(s));
    EXPECT_EQ(CALC_NO_RESULT, calc_undo(s));
    calc_close(s);
}

TEST(CalcApiTest, StatusText)
{
    EXPECT_STREQ("ok", calc_status_text(CALC_OK));
    EXPECT_STREQ("syntax error", calc_status_text(CALC_SYNTAX_ERROR));
    EXPECT_STREQ("division by zero", calc_status_text(CALC_DIVISION_BY_ZERO));
    EXPECT_STREQ("unknown status", calc_status_text(42));
}
