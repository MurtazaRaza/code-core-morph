/*
 * sbuf.c - a growable byte buffer, always NUL-terminated once it has data.
 */
#include <stdlib.h>
#include <string.h>

#include "wfint.h"

#define SBUF_MIN 16

void sbuf_init(sbuf *b)
{
    b->data = NULL;
    b->len = 0;
    b->cap = 0;
}

static wf_status sbuf_grow(sbuf *b, size_t need)
{
    size_t cap;
    char *data;

    if (need < b->len) /* the length wrapped around */
        return WF_ERANGE;
    if (need + 1 <= b->cap)
        return WF_OK;
    cap = b->cap ? b->cap : SBUF_MIN;
    while (cap < need + 1) {
        if (cap > ((size_t)-1) / 2)
            return WF_ERANGE;
        cap *= 2;
    }
    data = (char *)realloc(b->data, cap);
    if (data == NULL)
        return WF_ENOMEM;
    b->data = data;
    b->cap = cap;
    return WF_OK;
}

wf_status sbuf_append(sbuf *b, const char *s, size_t n)
{
    wf_status st;

    st = sbuf_grow(b, b->len + n);
    if (st != WF_OK)
        return st;
    memcpy(b->data + b->len, s, n);
    b->len += n;
    b->data[b->len] = '\0';
    return WF_OK;
}

wf_status sbuf_putc(sbuf *b, int c)
{
    char ch;

    ch = (char)c;
    return sbuf_append(b, &ch, 1);
}

void sbuf_reset(sbuf *b)
{
    b->len = 0;
    if (b->data != NULL)
        b->data[0] = '\0';
}

void sbuf_free(sbuf *b)
{
    free(b->data);
    sbuf_init(b);
}
