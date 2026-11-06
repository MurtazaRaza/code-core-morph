/*
 * kvconf - read "key = value" configuration files.
 *
 * A file has sections ("[server]") and entries ("port = 8080"). Values are
 * typed as they are read: integers, reals, booleans and strings.
 *
 * Copyright (c) 2009 The kvconf authors.  MIT license, see README.
 */
#ifndef KVCONF_H
#define KVCONF_H

#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

#define KV_VERSION "0.3.1"

#define KV_NAME_MAX 64          /* longest section or key name, with its NUL */
#define KV_MESSAGE_MAX 160      /* longest error or log message, with its NUL */

/* Status codes: 0 is success, errors are negative. */
typedef enum kv_status {
    KV_OK = 0,
    KV_ENOMEM = -1,
    KV_ESYNTAX = -2,
    KV_ERANGE = -3,
    KV_ENOTFOUND = -4,
    KV_ETYPE = -5,
    KV_EIO = -6
} kv_status;

/* Log levels, most severe first. */
enum {
    KV_LOG_ERROR = 0,
    KV_LOG_WARN = 1,
    KV_LOG_INFO = 2,
    KV_LOG_DEBUG = 3
};

typedef enum kv_type {
    KV_NONE = 0,
    KV_INT,
    KV_REAL,
    KV_BOOL,
    KV_STR
} kv_type;

#define KV_NTYPES 5

/* A typed value. Which member of u is set depends on type; a string is
 * malloc'd, NUL-terminated, and owned by the value. */
typedef struct kv_value {
    kv_type type;
    union {
        long i;
        double r;
        int b;
        struct {
            char *ptr;
            size_t len;
        } s;
    } u;
} kv_value;

/* One entry of a document. */
typedef struct kv_entry {
    char section[KV_NAME_MAX];  /* "" before the first section */
    char key[KV_NAME_MAX];
    kv_value value;
    int line;                   /* where it was last set */
} kv_entry;

/* Where and why parsing stopped. */
typedef struct kv_error {
    int code;                   /* a kv_status */
    int line;
    int column;                 /* 1-based; 0 when it is about the whole line */
    char message[KV_MESSAGE_MAX];
} kv_error;

/* A log sink: called with each formatted message. */
typedef void (*kv_log_fn)(void *ctx, int level, const char *message);

#define KV_OPT_CASE_FOLD 0x01u  /* section and key names are lowercased */
#define KV_OPT_ALLOW_DUP 0x02u  /* a key set again replaces it without a warning */
#define KV_OPT_NO_TYPES  0x04u  /* every value is a string */

/* Parser options; kv_options_init sets the defaults. */
typedef struct kv_options {
    unsigned flags;             /* KV_OPT_* */
    char comment;               /* starts a comment, '#' by default; ';' always does */
    int log_level;              /* messages less severe than this are dropped */
    kv_log_fn log;              /* NULL: messages go to stderr */
    void *log_ctx;
} kv_options;

/* A parsed document: its entries in the order they were first set. */
typedef struct kv_doc {
    kv_entry *entries;
    size_t count;
    size_t capacity;
    unsigned flags;             /* the options' flags it was parsed with */
    kv_error error;             /* why the last parse failed */
} kv_doc;

/* What a document holds, from kv_doc_stats. */
typedef struct kv_stats {
    size_t sections;            /* distinct section names, "" included */
    size_t entries;
    size_t by_type[KV_NTYPES];  /* indexed by kv_type */
    size_t longest_string;
} kv_stats;

/* Visits one entry; a non-zero return stops kv_each, which returns it. */
typedef int (*kv_visit_fn)(void *ctx, const kv_entry *entry);

/* Called by kv_scan for each entry, with its value as written (quotes
 * included); a non-zero return stops the scan, which returns it. */
typedef int (*kv_event_fn)(void *ctx, const char *section, const char *key,
                           const char *raw, int line);

/* Values (value.c). */
int kv_value_parse(kv_value *v, const char *text, size_t len, unsigned flags);
int kv_value_set_str(kv_value *v, const char *s, size_t len);
int kv_value_copy(kv_value *dst, const kv_value *src);
int kv_value_format(const kv_value *v, char *buf, size_t size);
void kv_value_clear(kv_value *v);
const char *kv_type_name(kv_type type);

/* Options and messages (log.c). */
void kv_options_init(kv_options *opt);
void kv_log(const kv_options *opt, int level, const char *fmt, ...);
void kv_vlog(const kv_options *opt, int level, const char *fmt, va_list ap);
void kv_log_write(const kv_options *opt, int level, const char *message);
const char *kv_level_name(int level);
const char *kv_strerror(int code);

/* Documents (doc.c). */
void kv_doc_init(kv_doc *doc);
void kv_doc_free(kv_doc *doc);
int kv_doc_set(kv_doc *doc, const char *section, const char *key,
               const kv_value *v, int line);
const kv_entry *kv_find(const kv_doc *doc, const char *section, const char *key);
int kv_get_int(const kv_doc *doc, const char *section, const char *key, long *out);
int kv_get_real(const kv_doc *doc, const char *section, const char *key, double *out);
int kv_get_bool(const kv_doc *doc, const char *section, const char *key, int *out);
const char *kv_get_str(const kv_doc *doc, const char *section, const char *key,
                       const char *fallback);
int kv_each(const kv_doc *doc, const char *section, kv_visit_fn fn, void *ctx);
void kv_doc_stats(const kv_doc *doc, kv_stats *stats);

/* Reading files (parse.c). */
int kv_scan(const char *text, size_t len, const kv_options *opt,
            kv_event_fn fn, void *ctx, kv_error *err);
int kv_parse_string(kv_doc *doc, const char *text, const kv_options *opt);
int kv_parse_file(kv_doc *doc, FILE *fp, const kv_options *opt);

#ifdef __cplusplus
}
#endif

#endif /* KVCONF_H */
