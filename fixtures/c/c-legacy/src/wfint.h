/*
 * wfint.h - the library's internals, shared by every source file.
 */
#ifndef WFINT_H
#define WFINT_H

#include "wf.h"

/* sbuf.c - a growable byte buffer */
typedef struct sbuf {
    char *data;
    size_t len;
    size_t cap;
} sbuf;

void sbuf_init(sbuf *b);
wf_status sbuf_append(sbuf *b, const char *s, size_t n);
wf_status sbuf_putc(sbuf *b, int c);
void sbuf_reset(sbuf *b);
void sbuf_free(sbuf *b);

/* htab.c - words and their counts, separate chaining */
typedef struct hentry {
    char *key;
    size_t len;
    unsigned long count;
    struct hentry *next;
} hentry;

typedef struct htab {
    hentry **buckets;
    size_t nbuckets;
    size_t size;
} htab;

typedef void (*htab_visit)(const hentry *e, void *ctx);

unsigned long wf_hash(const char *s, size_t n);
wf_status htab_init(htab *h, size_t nbuckets);
wf_status htab_add(htab *h, const char *key, size_t len, unsigned long n);
unsigned long htab_get(const htab *h, const char *key, size_t len);
void htab_each(const htab *h, htab_visit visit, void *ctx);
void htab_free(htab *h);

/* tok.c - splits text into words */
typedef struct tokenizer {
    const char *p;
    const char *end;
} tokenizer;

void tok_init(tokenizer *t, const char *text, size_t len);
/* The next word into word: *found is 1, or 0 at the end of the text. */
wf_status tok_next(tokenizer *t, sbuf *word, int *found);

/* conf.c - the settings */
extern size_t wf_conf_min_length;
extern int wf_conf_fold_case;

#endif
