/*
 * htab.c - words and their counts in a hash table with separate chaining.
 */
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "wfint.h"

#define HTAB_LOAD 3 /* grow past this many entries per bucket */

/* FNV-1a, 32 bits wide whatever the size of long. */
unsigned long wf_hash(const char *s, size_t n)
{
    unsigned long h;
    size_t i;

    h = 2166136261UL;
    for (i = 0; i < n; i++) {
        h ^= (unsigned char)s[i];
        h = (h * 16777619UL) & 0xffffffffUL;
    }
    return h;
}

wf_status htab_init(htab *h, size_t nbuckets)
{
    if (nbuckets == 0) {
        errno = EINVAL;
        return WF_EINVAL;
    }
    h->buckets = (hentry **)calloc(nbuckets, sizeof(hentry *));
    if (h->buckets == NULL)
        return WF_ENOMEM;
    h->nbuckets = nbuckets;
    h->size = 0;
    return WF_OK;
}

static hentry *htab_find(const htab *h, const char *key, size_t len)
{
    hentry *e;

    for (e = h->buckets[wf_hash(key, len) % h->nbuckets]; e != NULL; e = e->next)
        if (e->len == len && memcmp(e->key, key, len) == 0)
            return e;
    return NULL;
}

static void htab_rehash(htab *h)
{
    hentry **buckets;
    hentry *e;
    hentry *next;
    size_t n;
    size_t i;
    size_t slot;

    n = h->nbuckets * 2;
    buckets = (hentry **)calloc(n, sizeof(hentry *));
    if (buckets == NULL)
        return; /* keep the old table: slower, still right */
    for (i = 0; i < h->nbuckets; i++) {
        for (e = h->buckets[i]; e != NULL; e = next) {
            next = e->next;
            slot = wf_hash(e->key, e->len) % n;
            e->next = buckets[slot];
            buckets[slot] = e;
        }
    }
    free(h->buckets);
    h->buckets = buckets;
    h->nbuckets = n;
}

wf_status htab_add(htab *h, const char *key, size_t len, unsigned long n)
{
    hentry *e;
    size_t slot;

    e = htab_find(h, key, len);
    if (e != NULL) {
        if (e->count + n < e->count)
            return WF_ERANGE;
        e->count += n;
        return WF_OK;
    }
    e = (hentry *)malloc(sizeof(hentry));
    if (e == NULL)
        return WF_ENOMEM;
    e->key = (char *)malloc(len + 1);
    if (e->key == NULL) {
        free(e);
        return WF_ENOMEM;
    }
    memcpy(e->key, key, len);
    e->key[len] = '\0';
    e->len = len;
    e->count = n;
    slot = wf_hash(key, len) % h->nbuckets;
    e->next = h->buckets[slot];
    h->buckets[slot] = e;
    h->size++;
    if (h->size > h->nbuckets * HTAB_LOAD)
        htab_rehash(h);
    return WF_OK;
}

unsigned long htab_get(const htab *h, const char *key, size_t len)
{
    hentry *e;

    e = htab_find(h, key, len);
    return e != NULL ? e->count : 0;
}

void htab_each(const htab *h, htab_visit visit, void *ctx)
{
    hentry *e;
    size_t i;

    for (i = 0; i < h->nbuckets; i++)
        for (e = h->buckets[i]; e != NULL; e = e->next)
            visit(e, ctx);
}

void htab_free(htab *h)
{
    hentry *e;
    hentry *next;
    size_t i;

    for (i = 0; i < h->nbuckets; i++) {
        for (e = h->buckets[i]; e != NULL; e = next) {
            next = e->next;
            free(e->key);
            free(e);
        }
    }
    free(h->buckets);
    h->buckets = NULL;
    h->nbuckets = 0;
    h->size = 0;
}
