/*
 * value.c - typed values: what a value's text means, and back.
 */
#include "kvint.h"

#include <errno.h>
#include <math.h>

static const char *const type_names[KV_NTYPES] = {
    "none", "int", "real", "bool", "string"
};

const char *kv_type_name(kv_type type)
{
    if ((int)type < 0 || (int)type >= KV_NTYPES)
        return "?";
    return type_names[type];
}

void kv_value_clear(kv_value *v)
{
    if (v->type == KV_STR)
        free(v->u.s.ptr);
    memset(v, 0, sizeof *v);
    v->type = KV_NONE;
}

int kv_value_set_str(kv_value *v, const char *s, size_t len)
{
    char *copy = kvi_strndup(s, len);
    if (copy == NULL)
        return KV_ENOMEM;
    kv_value_clear(v);
    v->type = KV_STR;
    v->u.s.ptr = copy;
    v->u.s.len = len;
    return KV_OK;
}

int kv_value_copy(kv_value *dst, const kv_value *src)
{
    if (dst == src)
        return KV_OK;
    if (src->type == KV_STR)
        return kv_value_set_str(dst, src->u.s.ptr, src->u.s.len);
    kv_value_clear(dst);
    *dst = *src;
    return KV_OK;
}

static int parse_bool(const char *s, size_t len, int *out)
{
    static const char *const words[] = { "yes", "true", "on", "no", "false", "off" };
    size_t i;
    for (i = 0; i < KVI_COUNT(words); i++) {
        if (kvi_casencmp(s, words[i], len) == 0) {
            *out = i < 3;
            return 1;
        }
    }
    return 0;
}

/* 1: an integer, 0: not one, -1: one that doesn't fit a long. strtol's base
 * 0 reads "0x1f" as hex and "010" as octal; "08" isn't a number at all. */
static int parse_int(const char *s, size_t len, long *out)
{
    char buf[32], *end;
    size_t i = 0;
    long n;
    if (len == 0 || len >= sizeof buf)
        return 0;
    if (s[0] == '+' || s[0] == '-')
        i = 1;
    if (i == len)
        return 0;
    for (; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
              (c >= 'A' && c <= 'F') || c == 'x' || c == 'X'))
            return 0;
    }
    memcpy(buf, s, len);
    buf[len] = '\0';
    errno = 0;
    n = strtol(buf, &end, 0);
    if (end != buf + len)
        return 0;
    if (errno == ERANGE)
        return -1;
    *out = n;
    return 1;
}

/* Like parse_int, for numbers with a '.' or an exponent ("inf" and "nan"
 * are strings). */
static int parse_real(const char *s, size_t len, double *out)
{
    char buf[64], *end;
    size_t i;
    int point = 0;
    double r;
    if (len == 0 || len >= sizeof buf)
        return 0;
    for (i = 0; i < len; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '.' || c == 'e' || c == 'E')
            point = 1;
        else if (!((c >= '0' && c <= '9') || c == '+' || c == '-'))
            return 0;
    }
    if (!point)
        return 0;
    memcpy(buf, s, len);
    buf[len] = '\0';
    errno = 0;
    r = strtod(buf, &end);
    if (end != buf + len)
        return 0;
    if (errno == ERANGE && (r == HUGE_VAL || r == -HUGE_VAL))
        return -1;
    *out = r;
    return 1;
}

/* A quoted string: \" \\ \n and \t are the escapes. */
static int unquote(kv_value *v, const char *s, size_t len)
{
    char *out, *p;
    size_t i;
    if (len < 2 || s[len - 1] != '"')
        return KV_ESYNTAX;
    out = malloc(len);
    if (out == NULL)
        return KV_ENOMEM;
    p = out;
    for (i = 1; i < len - 1; i++) {
        char c = s[i];
        if (c == '"')
            goto bad;
        if (c == '\\') {
            if (++i == len - 1)
                goto bad;
            switch (s[i]) {
            case '"': c = '"'; break;
            case '\\': c = '\\'; break;
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            default: goto bad;
            }
        }
        *p++ = c;
    }
    *p = '\0';
    kv_value_clear(v);
    v->type = KV_STR;
    v->u.s.ptr = out;
    v->u.s.len = (size_t)(p - out);
    return KV_OK;
bad:
    free(out);
    return KV_ESYNTAX;
}

int kv_value_parse(kv_value *v, const char *text, size_t len, unsigned flags)
{
    long n;
    double r;
    int b, rc;
    len = kvi_trim(&text, len);
    if (len > 0 && text[0] == '"')
        return unquote(v, text, len);
    if (!(flags & KV_OPT_NO_TYPES)) {
        if (parse_bool(text, len, &b)) {
            kv_value_clear(v);
            v->type = KV_BOOL;
            v->u.b = b;
            return KV_OK;
        }
        rc = parse_int(text, len, &n);
        if (rc == 0)
            rc = parse_real(text, len, &r) * 2;
        if (rc < 0)
            return KV_ERANGE;
        if (rc == 1) {
            kv_value_clear(v);
            v->type = KV_INT;
            v->u.i = n;
            return KV_OK;
        }
        if (rc == 2) {
            kv_value_clear(v);
            v->type = KV_REAL;
            v->u.r = r;
            return KV_OK;
        }
    }
    return kv_value_set_str(v, text, len);
}

/* Formats a value as kvdump prints it; returns what snprintf does, the
 * length the whole text needs. A string is quoted, with its escapes. */
int kv_value_format(const kv_value *v, char *buf, size_t size)
{
    size_t i, n = 0;
    switch (v->type) {
    case KV_INT:
        return snprintf(buf, size, "%ld", v->u.i);
    case KV_REAL:
        return snprintf(buf, size, "%g", v->u.r);
    case KV_BOOL:
        return snprintf(buf, size, "%s", v->u.b ? "true" : "false");
    case KV_STR:
        break;
    default:
        return snprintf(buf, size, "%s", "");
    }
#define PUT(c) do { if (n + 1 < size) buf[n] = (c); n++; } while (0)
    PUT('"');
    for (i = 0; i < v->u.s.len; i++) {
        char c = v->u.s.ptr[i];
        switch (c) {
        case '"': PUT('\\'); PUT('"'); break;
        case '\\': PUT('\\'); PUT('\\'); break;
        case '\n': PUT('\\'); PUT('n'); break;
        case '\t': PUT('\\'); PUT('t'); break;
        default: PUT(c);
        }
    }
    PUT('"');
#undef PUT
    if (size > 0)
        buf[n < size ? n : size - 1] = '\0';
    return (int)n;
}
