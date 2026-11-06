/*
 * strutil.c - string helpers. ASCII only: names are ASCII, and the C
 * library's ctype functions depend on the locale.
 */
#include "kvint.h"

static int lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c - 'A' + 'a' : c;
}

/* A malloc'd copy of the first len bytes of s, NUL-terminated. */
char *kvi_strndup(const char *s, size_t len)
{
    char *copy = malloc(len + 1);
    if (copy == NULL)
        return NULL;
    memcpy(copy, s, len);
    copy[len] = '\0';
    return copy;
}

int kvi_casecmp(const char *a, const char *b)
{
    for (;; a++, b++) {
        int ca = lower((unsigned char)*a), cb = lower((unsigned char)*b);
        if (ca != cb || ca == '\0')
            return ca - cb;
    }
}

/* Compares the first len bytes of a with the whole of b. */
int kvi_casencmp(const char *a, const char *b, size_t len)
{
    size_t i;
    for (i = 0; i < len; i++) {
        int ca = lower((unsigned char)a[i]), cb = lower((unsigned char)b[i]);
        if (ca != cb)
            return ca - cb;
        if (cb == '\0')
            return 0;
    }
    return b[len] == '\0' ? 0 : -1;
}

/* Strips spaces and tabs from both ends: moves *s past the leading ones and
 * returns the length left. */
size_t kvi_trim(const char **s, size_t len)
{
    const char *p = *s;
    while (len > 0 && KVI_IS_SPACE(*p)) {
        p++;
        len--;
    }
    while (len > 0 && KVI_IS_SPACE(p[len - 1]))
        len--;
    *s = p;
    return len;
}

void kvi_lower(char *s)
{
    for (; *s; s++)
        *s = (char)lower((unsigned char)*s);
}

/* Names are letters, digits, '_', '-' and '.', and not empty. */
int kvi_valid_name(const char *s, size_t len)
{
    size_t i;
    if (len == 0)
        return 0;
    for (i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.'))
            return 0;
    }
    return 1;
}

/* Copies a name into a KV_NAME_MAX buffer, lowercased with KV_OPT_CASE_FOLD. */
int kvi_copy_name(char *dst, const char *src, size_t len, unsigned flags)
{
    if (len >= KV_NAME_MAX)
        return KV_ERANGE;
    memcpy(dst, src, len);
    dst[len] = '\0';
    if (flags & KV_OPT_CASE_FOLD)
        kvi_lower(dst);
    return KV_OK;
}
