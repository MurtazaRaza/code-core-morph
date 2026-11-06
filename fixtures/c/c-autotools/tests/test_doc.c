/* test_doc.c - documents: setting, finding, typed reads, visits, stats. */
#include <string.h>

#include "kvconf.h"
#include "check.h"

static kv_value value(const char *text)
{
    kv_value v = { KV_NONE };
    kv_value_parse(&v, text, strlen(text), 0);
    return v;
}

static int set(kv_doc *doc, const char *section, const char *key, const char *text, int line)
{
    kv_value v = value(text);
    int rc = kv_doc_set(doc, section, key, &v, line);
    kv_value_clear(&v);
    return rc;
}

static void test_set_and_find(void)
{
    kv_doc doc;
    const kv_entry *e;

    kv_doc_init(&doc);
    CHECK(doc.count == 0 && doc.entries == NULL);
    CHECK(set(&doc, NULL, "name", "\"demo\"", 1) == 0);
    CHECK(set(&doc, "server", "port", "8080", 3) == 0);
    CHECK(set(&doc, "server", "host", "localhost", 4) == 0);
    CHECK(set(&doc, "server", "port", "8081", 9) == 1);
    CHECK(doc.count == 3);
    CHECK_STR(doc.entries[1].key, "port");
    CHECK(doc.entries[1].value.u.i == 8081 && doc.entries[1].line == 9);
    e = kv_find(&doc, "", "name");
    CHECK(e != NULL && e == kv_find(&doc, NULL, "name"));
    CHECK(kv_find(&doc, "server", "name") == NULL);
    CHECK(kv_find(&doc, "Server", "port") == NULL);
    kv_doc_free(&doc);
    CHECK(doc.count == 0 && doc.entries == NULL && doc.capacity == 0);
}

static void test_many(void)
{
    kv_doc doc;
    char key[16];
    int i;

    kv_doc_init(&doc);
    for (i = 0; i < 100; i++) {
        sprintf(key, "k%d", i);
        set(&doc, "s", key, "1", i);
    }
    CHECK(doc.count == 100 && doc.capacity >= 100);
    CHECK_STR(doc.entries[99].key, "k99");
    kv_doc_free(&doc);
}

static void test_case_fold(void)
{
    kv_doc doc;

    kv_doc_init(&doc);
    doc.flags = KV_OPT_CASE_FOLD;
    CHECK(set(&doc, "Server", "Port", "1", 1) == 0);
    CHECK_STR(doc.entries[0].section, "server");
    CHECK_STR(doc.entries[0].key, "port");
    CHECK(kv_find(&doc, "SERVER", "pOrt") != NULL);
    CHECK(set(&doc, "server", "PORT", "2", 2) == 1);
    kv_doc_free(&doc);
}

static void test_long_names(void)
{
    kv_doc doc;
    char name[KV_NAME_MAX + 1];

    kv_doc_init(&doc);
    memset(name, 'n', KV_NAME_MAX);
    name[KV_NAME_MAX] = '\0';
    CHECK(set(&doc, "s", name, "1", 1) == KV_ERANGE);
    name[KV_NAME_MAX - 1] = '\0';
    CHECK(set(&doc, "s", name, "1", 1) == 0);
    CHECK(doc.count == 1);
    kv_doc_free(&doc);
}

static void test_typed_reads(void)
{
    kv_doc doc;
    long n = 0;
    double r = 0;
    int b = -1;

    kv_doc_init(&doc);
    set(&doc, "", "port", "8080", 1);
    set(&doc, "", "ratio", "0.5", 2);
    set(&doc, "", "debug", "on", 3);
    set(&doc, "", "name", "\"x\"", 4);
    CHECK(kv_get_int(&doc, NULL, "port", &n) == KV_OK && n == 8080);
    CHECK(kv_get_int(&doc, NULL, "ratio", &n) == KV_ETYPE && n == 8080);
    CHECK(kv_get_int(&doc, NULL, "missing", &n) == KV_ENOTFOUND);
    CHECK(kv_get_real(&doc, NULL, "ratio", &r) == KV_OK && r == 0.5);
    CHECK(kv_get_real(&doc, NULL, "port", &r) == KV_OK && r == 8080.0);
    CHECK(kv_get_real(&doc, NULL, "debug", &r) == KV_ETYPE);
    CHECK(kv_get_bool(&doc, NULL, "debug", &b) == KV_OK && b == 1);
    CHECK(kv_get_bool(&doc, NULL, "port", &b) == KV_ETYPE);
    CHECK_STR(kv_get_str(&doc, NULL, "name", "none"), "x");
    CHECK_STR(kv_get_str(&doc, NULL, "port", "none"), "none");
    CHECK(kv_get_str(&doc, NULL, "missing", NULL) == NULL);
    kv_doc_free(&doc);
}

struct visits {
    int seen;
    int stop_at;
    char keys[64];
};

static int visit(void *ctx, const kv_entry *e)
{
    struct visits *v = ctx;
    v->seen++;
    strcat(v->keys, e->key);
    return v->seen == v->stop_at ? 7 : 0;
}

static void test_each(void)
{
    kv_doc doc;
    struct visits v;

    kv_doc_init(&doc);
    set(&doc, "a", "x", "1", 1);
    set(&doc, "b", "y", "2", 2);
    set(&doc, "a", "z", "3", 3);
    memset(&v, 0, sizeof v);
    CHECK(kv_each(&doc, NULL, visit, &v) == 0 && v.seen == 3);
    CHECK_STR(v.keys, "xyz");
    memset(&v, 0, sizeof v);
    CHECK(kv_each(&doc, "a", visit, &v) == 0 && v.seen == 2);
    CHECK_STR(v.keys, "xz");
    memset(&v, 0, sizeof v);
    v.stop_at = 2;
    CHECK(kv_each(&doc, NULL, visit, &v) == 7 && v.seen == 2);
    kv_doc_free(&doc);
}

static void test_stats(void)
{
    kv_doc doc;
    kv_stats st;

    kv_doc_init(&doc);
    kv_doc_stats(&doc, &st);
    CHECK(st.entries == 0 && st.sections == 0);
    set(&doc, NULL, "a", "1", 1);
    set(&doc, "s", "b", "\"four\"", 2);
    set(&doc, "s", "c", "\"sixsix\"", 3);
    set(&doc, "t", "d", "yes", 4);
    kv_doc_stats(&doc, &st);
    CHECK(st.entries == 4 && st.sections == 3);
    CHECK(st.by_type[KV_INT] == 1 && st.by_type[KV_STR] == 2 && st.by_type[KV_BOOL] == 1);
    CHECK(st.by_type[KV_REAL] == 0 && st.longest_string == 6);
    kv_doc_free(&doc);
}

int main(void)
{
    test_set_and_find();
    test_many();
    test_case_fold();
    test_long_names();
    test_typed_reads();
    test_each();
    test_stats();
    return check_done("test_doc");
}
