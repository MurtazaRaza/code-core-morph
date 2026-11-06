/*
 * doc.c - documents: entries by section and key, in the order they came.
 */
#include "kvint.h"

#define KV_DOC_MIN 8

void kv_doc_init(kv_doc *doc)
{
    memset(doc, 0, sizeof *doc);
}

void kv_doc_free(kv_doc *doc)
{
    size_t i;
    for (i = 0; i < doc->count; i++)
        kv_value_clear(&doc->entries[i].value);
    free(doc->entries);
    kv_doc_init(doc);
}

static int same_name(const kv_doc *doc, const char *a, const char *b)
{
    if (doc->flags & KV_OPT_CASE_FOLD)
        return kvi_casecmp(a, b) == 0;
    return strcmp(a, b) == 0;
}

static kv_entry *lookup(const kv_doc *doc, const char *section, const char *key)
{
    size_t i;
    if (section == NULL)
        section = "";
    for (i = 0; i < doc->count; i++) {
        kv_entry *e = &doc->entries[i];
        if (same_name(doc, e->key, key) && same_name(doc, e->section, section))
            return e;
    }
    return NULL;
}

static int grow(kv_doc *doc)
{
    size_t capacity = doc->capacity ? doc->capacity * 2 : KV_DOC_MIN;
    kv_entry *entries = realloc(doc->entries, capacity * sizeof *entries);
    if (entries == NULL)
        return KV_ENOMEM;
    doc->entries = entries;
    doc->capacity = capacity;
    return KV_OK;
}

/* Sets a key: 0 when it is new, 1 when it replaced an earlier value (which
 * keeps its place), or an error. */
int kv_doc_set(kv_doc *doc, const char *section, const char *key,
               const kv_value *v, int line)
{
    kv_entry *e;
    int rc;
    if (section == NULL)
        section = "";
    e = lookup(doc, section, key);
    if (e != NULL) {
        rc = kv_value_copy(&e->value, v);
        if (rc != KV_OK)
            return rc;
        e->line = line;
        return 1;
    }
    if (doc->count == doc->capacity && grow(doc) != KV_OK)
        return KV_ENOMEM;
    e = &doc->entries[doc->count];
    memset(e, 0, sizeof *e);
    if (kvi_copy_name(e->section, section, strlen(section), doc->flags) != KV_OK ||
        kvi_copy_name(e->key, key, strlen(key), doc->flags) != KV_OK)
        return KV_ERANGE;
    rc = kv_value_copy(&e->value, v);
    if (rc != KV_OK)
        return rc;
    e->line = line;
    doc->count++;
    return 0;
}

const kv_entry *kv_find(const kv_doc *doc, const char *section, const char *key)
{
    return lookup(doc, section, key);
}

int kv_get_int(const kv_doc *doc, const char *section, const char *key, long *out)
{
    const kv_entry *e = lookup(doc, section, key);
    if (e == NULL)
        return KV_ENOTFOUND;
    if (e->value.type != KV_INT)
        return KV_ETYPE;
    *out = e->value.u.i;
    return KV_OK;
}

/* An integer is a real too. */
int kv_get_real(const kv_doc *doc, const char *section, const char *key, double *out)
{
    const kv_entry *e = lookup(doc, section, key);
    if (e == NULL)
        return KV_ENOTFOUND;
    if (e->value.type == KV_REAL)
        *out = e->value.u.r;
    else if (e->value.type == KV_INT)
        *out = (double)e->value.u.i;
    else
        return KV_ETYPE;
    return KV_OK;
}

int kv_get_bool(const kv_doc *doc, const char *section, const char *key, int *out)
{
    const kv_entry *e = lookup(doc, section, key);
    if (e == NULL)
        return KV_ENOTFOUND;
    if (e->value.type != KV_BOOL)
        return KV_ETYPE;
    *out = e->value.u.b;
    return KV_OK;
}

/* A string's text, or fallback when the key is missing or isn't a string. */
const char *kv_get_str(const kv_doc *doc, const char *section, const char *key,
                       const char *fallback)
{
    const kv_entry *e = lookup(doc, section, key);
    if (e == NULL || e->value.type != KV_STR)
        return fallback;
    return e->value.u.s.ptr;
}

/* Visits the entries of one section, or every entry when section is NULL. */
int kv_each(const kv_doc *doc, const char *section, kv_visit_fn fn, void *ctx)
{
    size_t i;
    for (i = 0; i < doc->count; i++) {
        const kv_entry *e = &doc->entries[i];
        int rc;
        if (section != NULL && !same_name(doc, e->section, section))
            continue;
        rc = fn(ctx, e);
        if (rc != 0)
            return rc;
    }
    return 0;
}

void kv_doc_stats(const kv_doc *doc, kv_stats *stats)
{
    size_t i, j;
    memset(stats, 0, sizeof *stats);
    stats->entries = doc->count;
    for (i = 0; i < doc->count; i++) {
        const kv_entry *e = &doc->entries[i];
        for (j = 0; j < i; j++) {
            if (same_name(doc, doc->entries[j].section, e->section))
                break;
        }
        if (j == i)
            stats->sections++;
        if ((int)e->value.type >= 0 && (int)e->value.type < KV_NTYPES)
            stats->by_type[e->value.type]++;
        if (e->value.type == KV_STR && e->value.u.s.len > stats->longest_string)
            stats->longest_string = e->value.u.s.len;
    }
}
