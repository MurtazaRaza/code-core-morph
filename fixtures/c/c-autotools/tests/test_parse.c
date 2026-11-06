/* test_parse.c - reading text and files into documents. */
#include <stdio.h>
#include <string.h>

#include "kvconf.h"
#include "check.h"

struct events {
    int count;
    int stop_at;
    char log[512];
};

static int record(void *ctx, const char *section, const char *key, const char *raw, int line)
{
    struct events *ev = ctx;
    char one[160];
    ev->count++;
    sprintf(one, "%d:%s/%s=%s|", line, section, key, raw);
    strcat(ev->log, one);
    return ev->count == ev->stop_at ? 99 : 0;
}

static int scan(const char *text, const kv_options *opt, struct events *ev, kv_error *err)
{
    memset(ev, 0, sizeof *ev);
    return kv_scan(text, strlen(text), opt, record, ev, err);
}

struct sink {
    int calls;
    char last[KV_MESSAGE_MAX];
};

static void collect(void *ctx, int level, const char *message)
{
    struct sink *s = ctx;
    (void)level;
    s->calls++;
    strcpy(s->last, message);
}

static void quiet(kv_options *opt, struct sink *s)
{
    memset(s, 0, sizeof *s);
    kv_options_init(opt);
    opt->log = collect;
    opt->log_ctx = s;
}

static void test_scan(void)
{
    struct events ev;
    kv_error err;
    kv_options opt;
    struct sink s;

    quiet(&opt, &s);
    CHECK(scan("a = 1\r\n; note\n# note\n\n[ srv ]  # main\nb=two words  # c\n"
               "c = \"q;#\"  ; after\nd = x#y\n", &opt, &ev, &err) == KV_OK);
    CHECK_STR(ev.log, "1:/a=1|6:srv/b=two words|7:srv/c=\"q;#\"|8:srv/d=x#y|");
    CHECK(err.code == KV_OK && s.calls == 0);
    opt.comment = '!';
    CHECK(scan("a = 1 ! no\n! skip\nb = 2 # kept\n", &opt, &ev, NULL) == KV_OK);
    CHECK_STR(ev.log, "1:/a=1|3:/b=2 # kept|");
    CHECK(scan("a = 1\nb = 2\nc = 3\n", NULL, &ev, NULL) == KV_OK && ev.count == 3);
    memset(&ev, 0, sizeof ev);
    ev.stop_at = 2;
    CHECK(kv_scan("a = 1\nb = 2\nc = 3\n", 18, NULL, record, &ev, NULL) == 99);
    CHECK(ev.count == 2);
    CHECK(scan("a = 1\nb = 2", NULL, &ev, NULL) == KV_OK && ev.count == 2);
    CHECK(scan("x = 1\ny = 2\n", NULL, &ev, NULL) == KV_OK);
    memset(&ev, 0, sizeof ev);
    CHECK(kv_scan("x = 1\ny = 2\n", 6, NULL, record, &ev, NULL) == KV_OK && ev.count == 1);
}

static void test_errors(void)
{
    struct events ev;
    kv_error err;
    kv_options opt;
    struct sink s;

    quiet(&opt, &s);
    CHECK(scan("a = 1\n  [open\n", &opt, &ev, &err) == KV_ESYNTAX);
    CHECK(err.code == KV_ESYNTAX && err.line == 2 && err.column == 3);
    CHECK_STR(err.message, "missing ']'");
    CHECK(s.calls == 1);
    CHECK_STR(s.last, "line 2, column 3: missing ']'");
    CHECK(scan("just words\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 0);
    CHECK_STR(err.message, "expected '='");
    CHECK_STR(s.last, "line 1: expected '='");
    CHECK(scan("bad key = 1\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 1);
    CHECK_STR(err.message, "bad key name");
    CHECK(scan("[a b]\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 2);
    CHECK(scan("[a] x\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 4);
    CHECK_STR(err.message, "text after ']'");
    CHECK(scan("k = \"a\" b\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 8);
    CHECK_STR(err.message, "text after the closing quote");
    CHECK(scan("k = \"a\\\"\n", &opt, &ev, &err) == KV_ESYNTAX && err.column == 5);
    CHECK_STR(err.message, "missing '\"'");
    CHECK(scan("kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk = 1\n",
               &opt, &ev, &err) == KV_ERANGE);
    CHECK_STR(err.message, "key name too long");
}

static void test_parse_string(void)
{
    kv_doc doc;
    kv_options opt;
    struct sink s;
    long n = 0;

    quiet(&opt, &s);
    kv_doc_init(&doc);
    CHECK(kv_parse_string(&doc, "top = yes\n[net]\nport = 80\nport = 81\nhost = \"h\"\n",
                          &opt) == KV_OK);
    CHECK(doc.count == 3);
    CHECK(kv_get_int(&doc, "net", "port", &n) == KV_OK && n == 81);
    CHECK(s.calls == 1);
    CHECK_STR(s.last, "line 4: net.port is set again");
    kv_doc_free(&doc);

    quiet(&opt, &s);
    opt.flags = KV_OPT_ALLOW_DUP | KV_OPT_CASE_FOLD;
    kv_doc_init(&doc);
    CHECK(kv_parse_string(&doc, "[Net]\nPort = 80\nport = 81\n", &opt) == KV_OK);
    CHECK(doc.count == 1 && s.calls == 0 && doc.flags == opt.flags);
    CHECK_STR(doc.entries[0].section, "net");
    kv_doc_free(&doc);

    quiet(&opt, &s);
    kv_doc_init(&doc);
    CHECK(kv_parse_string(&doc, "a = 1\nbig = 1e999\n", &opt) == KV_ERANGE);
    CHECK(doc.error.code == KV_ERANGE && doc.error.line == 2 && doc.error.column == 0);
    CHECK_STR(doc.error.message, "big: value out of range");
    CHECK_STR(s.last, "line 2: big: value out of range");
    CHECK(doc.count == 1);
    kv_doc_free(&doc);

    kv_doc_init(&doc);
    CHECK(kv_parse_string(&doc, "", NULL) == KV_OK && doc.count == 0);
    kv_doc_free(&doc);
}

static void test_parse_file(void)
{
    kv_doc doc;
    FILE *fp = tmpfile();
    int i;

    CHECK(fp != NULL);
    if (fp == NULL)
        return;
    for (i = 0; i < 500; i++)
        fprintf(fp, "[s%d]\nkey = \"value number %d\"\n", i, i);
    rewind(fp);
    kv_doc_init(&doc);
    CHECK(kv_parse_file(&doc, fp, NULL) == KV_OK);
    CHECK(doc.count == 500);
    CHECK_STR(kv_get_str(&doc, "s499", "key", ""), "value number 499");
    kv_doc_free(&doc);
    fclose(fp);
}

int main(void)
{
    test_scan();
    test_errors();
    test_parse_string();
    test_parse_file();
    return check_done("test_parse");
}
