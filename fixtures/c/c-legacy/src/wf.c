/*
 * wf.c - the public interface: tables of counted words.
 */
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "wfint.h"

#define WF_BUCKETS 64

struct wf_table {
    htab words;
};

wf_status wf_table_new(wf_table **out)
{
    wf_table *table;
    wf_status st;

    if (out == NULL) {
        errno = EINVAL;
        return WF_EINVAL;
    }
    table = (wf_table *)malloc(sizeof(wf_table));
    if (table == NULL)
        return WF_ENOMEM;
    st = htab_init(&table->words, WF_BUCKETS);
    if (st != WF_OK) {
        free(table);
        return st;
    }
    *out = table;
    return WF_OK;
}

void wf_table_free(wf_table *table)
{
    if (table == NULL)
        return;
    htab_free(&table->words);
    free(table);
}

wf_status wf_count_text(wf_table *table, const char *text, size_t len)
{
    tokenizer t;
    sbuf word;
    wf_status st;
    int found;

    if (table == NULL || (text == NULL && len > 0)) {
        errno = EINVAL;
        return WF_EINVAL;
    }
    sbuf_init(&word);
    tok_init(&t, text, len);
    for (;;) {
        st = tok_next(&t, &word, &found);
        if (st != WF_OK)
            goto done;
        if (!found)
            break;
        st = htab_add(&table->words, word.data, word.len, 1);
        if (st != WF_OK)
            goto done;
    }
    st = WF_OK;
done:
    sbuf_free(&word);
    return st;
}

size_t wf_table_size(const wf_table *table)
{
    return table != NULL ? table->words.size : 0;
}

unsigned long wf_lookup(const wf_table *table, const char *word)
{
    if (table == NULL || word == NULL)
        return 0;
    return htab_get(&table->words, word, strlen(word));
}

struct collect {
    wf_word *words;
    size_t n;
};

static void collect_one(const hentry *e, void *ctx)
{
    struct collect *c;

    c = (struct collect *)ctx;
    c->words[c->n].text = e->key;
    c->words[c->n].len = e->len;
    c->words[c->n].count = e->count;
    c->n++;
}

static int by_count(const void *a, const void *b)
{
    const wf_word *x;
    const wf_word *y;

    x = (const wf_word *)a;
    y = (const wf_word *)b;
    if (x->count != y->count)
        return x->count > y->count ? -1 : 1;
    return strcmp(x->text, y->text);
}

size_t wf_top(const wf_table *table, wf_word *out, size_t n)
{
    struct collect c;
    size_t size;

    if (table == NULL || (out == NULL && n > 0)) {
        errno = EINVAL;
        return 0;
    }
    size = table->words.size;
    if (size == 0 || n == 0)
        return 0;
    c.words = (wf_word *)malloc(size * sizeof(wf_word));
    if (c.words == NULL) {
        errno = ENOMEM;
        return 0;
    }
    c.n = 0;
    htab_each(&table->words, collect_one, &c);
    qsort(c.words, c.n, sizeof(wf_word), by_count);
    if (n > c.n)
        n = c.n;
    memcpy(out, c.words, n * sizeof(wf_word));
    free(c.words);
    return n;
}

const char *wf_strerror(wf_status status)
{
    switch (status) {
    case WF_OK:
        return "no error";
    case WF_ENOMEM:
        return "out of memory";
    case WF_EINVAL:
        return "invalid argument";
    case WF_ERANGE:
        return "count out of range";
    }
    return "unknown error";
}
