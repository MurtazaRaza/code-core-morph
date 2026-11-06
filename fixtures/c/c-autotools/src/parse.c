/*
 * parse.c - reading files: kv_scan finds the entries, kv_parse_string and
 * kv_parse_file keep them in a document.
 *
 * A line is blank, a comment (';', or the options' comment character), a
 * section ("[name]") or an entry ("key = value"). An unquoted value ends
 * at a comment character that follows a space; a quoted one at its
 * closing quote.
 */
#include "kvint.h"

struct scan {
    const kv_options *opt;
    kv_error *err;
    const char *line;           /* the current line, for columns */
    int number;
};

static int column(const struct scan *sc, const char *at)
{
    return (int)(at - sc->line) + 1;
}

/* Records why the scan stops and logs it; column 0 is the whole line. */
static int fail(struct scan *sc, int col, int code, const char *what)
{
    if (sc->err != NULL) {
        sc->err->code = code;
        sc->err->line = sc->number;
        sc->err->column = col;
        snprintf(sc->err->message, sizeof sc->err->message, "%s", what);
    }
    if (col > 0)
        kv_log(sc->opt, KV_LOG_ERROR, "line %d, column %d: %s", sc->number, col, what);
    else
        kv_log(sc->opt, KV_LOG_ERROR, "line %d: %s", sc->number, what);
    return code;
}

static int is_comment(const kv_options *opt, char c)
{
    return c == ';' || c == opt->comment;
}

/* Only a comment, or nothing, may follow a section's ']' or a closing quote. */
static int blank_rest(const kv_options *opt, const char *s, size_t len)
{
    len = kvi_trim(&s, len);
    return len == 0 || is_comment(opt, s[0]);
}

static int section_line(struct scan *sc, const char *s, size_t len, char *section)
{
    const char *close = memchr(s, ']', len), *name = s + 1;
    size_t n;
    if (close == NULL)
        return fail(sc, column(sc, s), KV_ESYNTAX, "missing ']'");
    if (!blank_rest(sc->opt, close + 1, len - (size_t)(close + 1 - s)))
        return fail(sc, column(sc, close + 1), KV_ESYNTAX, "text after ']'");
    n = kvi_trim(&name, (size_t)(close - name));
    if (!kvi_valid_name(name, n))
        return fail(sc, column(sc, name), KV_ESYNTAX, "bad section name");
    if (kvi_copy_name(section, name, n, sc->opt->flags) != KV_OK)
        return fail(sc, column(sc, name), KV_ERANGE, "section name too long");
    kv_log(sc->opt, KV_LOG_DEBUG, "line %d: section %s", sc->number, section);
    return KV_OK;
}

/* The value as written: trimmed, with any comment after it cut off. */
static int value_text(struct scan *sc, const char **v, size_t *len)
{
    const char *s = *v;
    size_t n = kvi_trim(&s, *len), i;
    if (n > 0 && s[0] == '"') {
        for (i = 1; i < n && s[i] != '"'; i++) {
            if (s[i] == '\\' && i + 1 < n)
                i++;
        }
        if (i >= n)
            return fail(sc, column(sc, s), KV_ESYNTAX, "missing '\"'");
        if (!blank_rest(sc->opt, s + i + 1, n - i - 1))
            return fail(sc, column(sc, s + i + 1), KV_ESYNTAX, "text after the closing quote");
        n = i + 1;
    } else {
        for (i = 1; i < n; i++) {
            if (is_comment(sc->opt, s[i]) && KVI_IS_SPACE(s[i - 1]))
                break;
        }
        n = kvi_trim(&s, n < i ? n : i);
    }
    *v = s;
    *len = n;
    return KV_OK;
}

int kv_scan(const char *text, size_t len, const kv_options *opt,
            kv_event_fn fn, void *ctx, kv_error *err)
{
    kv_options defaults;
    struct scan sc;
    char section[KV_NAME_MAX] = "", key[KV_NAME_MAX];
    const char *p = text, *end = text + len;
    int rc;

    if (opt == NULL) {
        kv_options_init(&defaults);
        opt = &defaults;
    }
    sc.opt = opt;
    sc.err = err;
    sc.number = 0;
    if (err != NULL)
        memset(err, 0, sizeof *err);
    while (p < end) {
        const char *eol = memchr(p, '\n', (size_t)(end - p)), *s = p, *eq, *k, *v;
        size_t n = (size_t)((eol != NULL ? eol : end) - p), klen, vlen;
        char *raw;

        sc.line = p;
        sc.number++;
        p = eol != NULL ? eol + 1 : end;
        if (n > 0 && s[n - 1] == '\r')
            n--;
        n = kvi_trim(&s, n);
        if (n == 0 || is_comment(opt, s[0]))
            continue;
        if (s[0] == '[') {
            rc = section_line(&sc, s, n, section);
            if (rc != KV_OK)
                return rc;
            continue;
        }
        eq = memchr(s, '=', n);
        if (eq == NULL)
            return fail(&sc, 0, KV_ESYNTAX, "expected '='");
        k = s;
        klen = kvi_trim(&k, (size_t)(eq - s));
        if (!kvi_valid_name(k, klen))
            return fail(&sc, column(&sc, k), KV_ESYNTAX, "bad key name");
        if (kvi_copy_name(key, k, klen, opt->flags) != KV_OK)
            return fail(&sc, column(&sc, k), KV_ERANGE, "key name too long");
        v = eq + 1;
        vlen = (size_t)(s + n - v);
        rc = value_text(&sc, &v, &vlen);
        if (rc != KV_OK)
            return rc;
        raw = kvi_strndup(v, vlen);
        if (raw == NULL)
            return fail(&sc, 0, KV_ENOMEM, "out of memory");
        rc = fn(ctx, section, key, raw, sc.number);
        free(raw);
        if (rc != 0)
            return rc;
    }
    return KV_OK;
}

struct build {
    kv_doc *doc;
    const kv_options *opt;
};

static int add_entry(void *ctx, const char *section, const char *key,
                     const char *raw, int line)
{
    struct build *b = ctx;
    kv_error *err = &b->doc->error;
    kv_value v;
    int rc;

    memset(&v, 0, sizeof v);
    rc = kv_value_parse(&v, raw, strlen(raw), b->opt->flags);
    if (rc == KV_OK) {
        rc = kv_doc_set(b->doc, section, key, &v, line);
        kv_value_clear(&v);
    }
    if (rc == 1 && !(b->opt->flags & KV_OPT_ALLOW_DUP))
        kv_log(b->opt, KV_LOG_WARN, "line %d: %s%s%s is set again", line, section,
               section[0] != '\0' ? "." : "", key);
    if (rc >= 0)
        return 0;
    err->code = rc;
    err->line = line;
    err->column = 0;
    snprintf(err->message, sizeof err->message, "%s: %s", key, kv_strerror(rc));
    kv_log(b->opt, KV_LOG_ERROR, "line %d: %s", line, err->message);
    return rc;
}

int kv_parse_string(kv_doc *doc, const char *text, const kv_options *opt)
{
    kv_options defaults;
    struct build b;

    if (opt == NULL) {
        kv_options_init(&defaults);
        opt = &defaults;
    }
    doc->flags = opt->flags;
    b.doc = doc;
    b.opt = opt;
    return kv_scan(text, strlen(text), opt, add_entry, &b, &doc->error);
}

int kv_parse_file(kv_doc *doc, FILE *fp, const kv_options *opt)
{
    char *buf = NULL, *bigger;
    size_t len = 0, cap = 0, got;
    int rc;

    for (;;) {
        if (cap - len < 1024) {
            cap = cap != 0 ? cap * 2 : 4096;
            bigger = realloc(buf, cap + 1);
            if (bigger == NULL) {
                rc = KV_ENOMEM;
                goto out;
            }
            buf = bigger;
        }
        got = fread(buf + len, 1, cap - len, fp);
        len += got;
        if (got == 0)
            break;
    }
    if (ferror(fp)) {
        memset(&doc->error, 0, sizeof doc->error);
        doc->error.code = KV_EIO;
        snprintf(doc->error.message, sizeof doc->error.message, "%s", kv_strerror(KV_EIO));
        rc = KV_EIO;
        goto out;
    }
    buf[len] = '\0';
    rc = kv_parse_string(doc, buf, opt);
out:
    free(buf);
    return rc;
}
