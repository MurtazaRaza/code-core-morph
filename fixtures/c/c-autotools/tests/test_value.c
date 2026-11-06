/* test_value.c - what values' texts mean, and how values print. */
#include <stdlib.h>
#include <string.h>

#include "kvconf.h"
#include "check.h"

static int parse(kv_value *v, const char *text, unsigned flags)
{
    return kv_value_parse(v, text, strlen(text), flags);
}

static void test_integers(void)
{
    kv_value v = { KV_NONE };

    CHECK(parse(&v, "8080", 0) == KV_OK && v.type == KV_INT && v.u.i == 8080);
    CHECK(parse(&v, "  -42 ", 0) == KV_OK && v.type == KV_INT && v.u.i == -42);
    CHECK(parse(&v, "0x1f", 0) == KV_OK && v.type == KV_INT && v.u.i == 31);
    CHECK(parse(&v, "010", 0) == KV_OK && v.type == KV_INT && v.u.i == 8);
    CHECK(parse(&v, "08", 0) == KV_OK && v.type == KV_STR);
    CHECK_STR(v.u.s.ptr, "08");
    CHECK(parse(&v, "99999999999999999999", 0) == KV_ERANGE);
    CHECK(v.type == KV_STR);
    kv_value_clear(&v);
}

static void test_reals(void)
{
    kv_value v = { KV_NONE };

    CHECK(parse(&v, "2.5", 0) == KV_OK && v.type == KV_REAL && v.u.r == 2.5);
    CHECK(parse(&v, "-.5e-3", 0) == KV_OK && v.type == KV_REAL && v.u.r == -0.0005);
    CHECK(parse(&v, "1e5", 0) == KV_OK && v.type == KV_REAL && v.u.r == 100000.0);
    CHECK(parse(&v, "1e999", 0) == KV_ERANGE);
    CHECK(parse(&v, "inf", 0) == KV_OK && v.type == KV_STR);
    kv_value_clear(&v);
}

static void test_booleans(void)
{
    kv_value v = { KV_NONE };

    CHECK(parse(&v, "yes", 0) == KV_OK && v.type == KV_BOOL && v.u.b == 1);
    CHECK(parse(&v, "On", 0) == KV_OK && v.type == KV_BOOL && v.u.b == 1);
    CHECK(parse(&v, "FALSE", 0) == KV_OK && v.type == KV_BOOL && v.u.b == 0);
    CHECK(parse(&v, "y", 0) == KV_OK && v.type == KV_STR);
    kv_value_clear(&v);
}

static void test_strings(void)
{
    kv_value v = { KV_NONE };

    CHECK(parse(&v, "\"say \\\"hi\\\"\\tnow\"", 0) == KV_OK && v.type == KV_STR);
    CHECK_STR(v.u.s.ptr, "say \"hi\"\tnow");
    CHECK(v.u.s.len == 12);
    CHECK(parse(&v, "\"bad \\q\"", 0) == KV_ESYNTAX);
    CHECK(parse(&v, "\"open", 0) == KV_ESYNTAX);
    CHECK(parse(&v, "", 0) == KV_OK && v.type == KV_STR && v.u.s.len == 0);
    CHECK(parse(&v, "8080", KV_OPT_NO_TYPES) == KV_OK && v.type == KV_STR);
    CHECK_STR(v.u.s.ptr, "8080");
    CHECK(kv_value_set_str(&v, "abcdef", 3) == KV_OK && v.u.s.len == 3);
    CHECK_STR(v.u.s.ptr, "abc");
    kv_value_clear(&v);
    CHECK(v.type == KV_NONE);
}

static void test_copy(void)
{
    kv_value a = { KV_NONE }, b = { KV_NONE };

    parse(&a, "\"text\"", 0);
    CHECK(kv_value_copy(&b, &a) == KV_OK && b.type == KV_STR);
    CHECK(b.u.s.ptr != a.u.s.ptr);
    CHECK_STR(b.u.s.ptr, "text");
    CHECK(kv_value_copy(&a, &a) == KV_OK);
    CHECK_STR(a.u.s.ptr, "text");
    parse(&a, "7", 0);
    CHECK(kv_value_copy(&b, &a) == KV_OK && b.type == KV_INT && b.u.i == 7);
    kv_value_clear(&a);
    kv_value_clear(&b);
}

static const char *formatted(const char *text)
{
    static char buf[64];
    kv_value v = { KV_NONE };
    parse(&v, text, 0);
    kv_value_format(&v, buf, sizeof buf);
    kv_value_clear(&v);
    return buf;
}

static void test_format(void)
{
    kv_value v = { KV_NONE };
    char buf[4];

    CHECK_STR(formatted("-12"), "-12");
    CHECK_STR(formatted("2.5"), "2.5");
    CHECK_STR(formatted("1e-5"), "1e-05");
    CHECK_STR(formatted("100000.0"), "100000");
    CHECK_STR(formatted("1e6"), "1e+06");
    CHECK_STR(formatted("0.1234567"), "0.123457");
    CHECK_STR(formatted("off"), "false");
    CHECK_STR(formatted("\"a\\\"b\\nc\""), "\"a\\\"b\\nc\"");
    parse(&v, "\"abc\"", 0);
    CHECK(kv_value_format(&v, buf, sizeof buf) == 5);
    CHECK_STR(buf, "\"ab");
    CHECK(kv_value_format(&v, NULL, 0) == 5);
    kv_value_clear(&v);
    CHECK(kv_value_format(&v, buf, sizeof buf) == 0 && buf[0] == '\0');
}

static void test_type_names(void)
{
    CHECK_STR(kv_type_name(KV_INT), "int");
    CHECK_STR(kv_type_name(KV_STR), "string");
    CHECK_STR(kv_type_name(KV_NONE), "none");
    CHECK_STR(kv_type_name((kv_type)9), "?");
}

int main(void)
{
    test_integers();
    test_reals();
    test_booleans();
    test_strings();
    test_copy();
    test_format();
    test_type_names();
    return check_done("test_value");
}
