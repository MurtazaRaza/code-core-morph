/*
 * log.c - options, messages and the log.
 */
#include "kvint.h"

static const char *const level_names[] = { "error", "warning", "info", "debug" };

void kv_options_init(kv_options *opt)
{
    opt->flags = 0;
    opt->comment = '#';
    opt->log_level = KV_LOG_WARN;
    opt->log = NULL;
    opt->log_ctx = NULL;
}

const char *kv_level_name(int level)
{
    if (level < 0 || level >= (int)KVI_COUNT(level_names))
        return "unknown";
    return level_names[level];
}

const char *kv_strerror(int code)
{
    switch (code) {
    case KV_OK: return "no error";
    case KV_ENOMEM: return "out of memory";
    case KV_ESYNTAX: return "syntax error";
    case KV_ERANGE: return "value out of range";
    case KV_ENOTFOUND: return "no such key";
    case KV_ETYPE: return "wrong type";
    case KV_EIO: return "read error";
    default: return "unknown error";
    }
}

static int enabled(const kv_options *opt, int level)
{
    return level <= (opt != NULL ? opt->log_level : KV_LOG_WARN);
}

/* A message that is already formatted: to the options' sink, or stderr. */
void kv_log_write(const kv_options *opt, int level, const char *message)
{
    if (!enabled(opt, level))
        return;
    if (opt != NULL && opt->log != NULL) {
        opt->log(opt->log_ctx, level, message);
        return;
    }
    fprintf(stderr, "kvconf: %s: %s\n", kv_level_name(level), message);
}

void kv_vlog(const kv_options *opt, int level, const char *fmt, va_list ap)
{
    char buf[KV_MESSAGE_MAX];
    if (!enabled(opt, level))
        return;
    vsnprintf(buf, sizeof buf, fmt, ap); /* a longer message is cut short */
    kv_log_write(opt, level, buf);
}

void kv_log(const kv_options *opt, int level, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    kv_vlog(opt, level, fmt, ap);
    va_end(ap);
}
