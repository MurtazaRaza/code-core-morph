/* test_log.c - options, messages and the log's sink. */
#include <stdarg.h>
#include <string.h>

#include "kvconf.h"
#include "check.h"

struct sink {
    int calls;
    int level;
    char last[KV_MESSAGE_MAX + 16];
};

static void collect(void *ctx, int level, const char *message)
{
    struct sink *s = ctx;
    s->calls++;
    s->level = level;
    strncpy(s->last, message, sizeof s->last - 1);
    s->last[sizeof s->last - 1] = '\0';
}

static void options(kv_options *opt, struct sink *s)
{
    memset(s, 0, sizeof *s);
    kv_options_init(opt);
    opt->log = collect;
    opt->log_ctx = s;
}

static void vlog(const kv_options *opt, int level, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    kv_vlog(opt, level, fmt, ap);
    va_end(ap);
}

static void test_defaults(void)
{
    kv_options opt;
    memset(&opt, 0x55, sizeof opt);
    kv_options_init(&opt);
    CHECK(opt.flags == 0);
    CHECK(opt.comment == '#');
    CHECK(opt.log_level == KV_LOG_WARN);
    CHECK(opt.log == NULL && opt.log_ctx == NULL);
}

static void test_sink(void)
{
    kv_options opt;
    struct sink s;

    options(&opt, &s);
    kv_log(&opt, KV_LOG_WARN, "line %d: %s is set again", 3, "port");
    CHECK(s.calls == 1 && s.level == KV_LOG_WARN);
    CHECK_STR(s.last, "line 3: port is set again");
    vlog(&opt, KV_LOG_ERROR, "%s=%05.1f", "ratio", 2.25);
    CHECK(s.calls == 2 && s.level == KV_LOG_ERROR);
    CHECK_STR(s.last, "ratio=002.2");
    kv_log_write(&opt, KV_LOG_WARN, "100% done");
    CHECK_STR(s.last, "100% done");
}

static void test_levels(void)
{
    kv_options opt;
    struct sink s;

    options(&opt, &s);
    kv_log(&opt, KV_LOG_INFO, "dropped");
    kv_log(&opt, KV_LOG_DEBUG, "dropped");
    kv_log_write(&opt, KV_LOG_INFO, "dropped");
    CHECK(s.calls == 0);
    opt.log_level = KV_LOG_DEBUG;
    kv_log(&opt, KV_LOG_DEBUG, "kept");
    CHECK(s.calls == 1 && s.level == KV_LOG_DEBUG);
    opt.log_level = -1;
    kv_log(&opt, KV_LOG_ERROR, "dropped");
    CHECK(s.calls == 1);
}

static void test_long_message(void)
{
    kv_options opt;
    struct sink s;
    char word[KV_MESSAGE_MAX * 2];

    options(&opt, &s);
    memset(word, 'x', sizeof word - 1);
    word[sizeof word - 1] = '\0';
    kv_log(&opt, KV_LOG_ERROR, "%s", word);
    CHECK(strlen(s.last) == KV_MESSAGE_MAX - 1);
    kv_log_write(&opt, KV_LOG_ERROR, word);
    CHECK(strlen(s.last) == sizeof s.last - 1);
}

static void test_names(void)
{
    CHECK_STR(kv_level_name(KV_LOG_ERROR), "error");
    CHECK_STR(kv_level_name(KV_LOG_WARN), "warning");
    CHECK_STR(kv_level_name(KV_LOG_DEBUG), "debug");
    CHECK_STR(kv_level_name(7), "unknown");
    CHECK_STR(kv_strerror(KV_OK), "no error");
    CHECK_STR(kv_strerror(KV_ERANGE), "value out of range");
    CHECK_STR(kv_strerror(KV_EIO), "read error");
    CHECK_STR(kv_strerror(42), "unknown error");
}

int main(void)
{
    test_defaults();
    test_sink();
    test_levels();
    test_long_message();
    test_names();
    return check_done("test_log");
}
