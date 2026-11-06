/*
 * kvdump - print a configuration file's entries with their types.
 *
 * usage: kvdump [-f] [-t] [-q | -v] [-s section] file
 *
 *   -f          fold section and key names to lowercase
 *   -t          no types: every value is a string
 *   -q, -v      errors only; or debug messages too
 *   -s section  only that section's entries
 *
 * Each entry is printed as "section.key = type:value", in the file's order,
 * then a summary line. Messages go to stderr; a file that doesn't parse
 * exits with status 1.
 */
#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "kvconf.h"

struct dump {
    FILE *out;
    const char *file;
    int shown;
};

static void to_stderr(void *ctx, int level, const char *message)
{
    const struct dump *d = ctx;
    fprintf(stderr, "kvdump: %s: %s: %s\n", d->file, kv_level_name(level), message);
}

static int print_entry(void *ctx, const kv_entry *e)
{
    struct dump *d = ctx;
    char small[64], *text = small;
    int n = kv_value_format(&e->value, small, sizeof small);

    if (n < 0)
        return -1;
    if ((size_t)n >= sizeof small) {
        text = malloc((size_t)n + 1);
        if (text == NULL)
            return KV_ENOMEM;
        kv_value_format(&e->value, text, (size_t)n + 1);
    }
    fprintf(d->out, "%s%s%s = %s:%s\n", e->section, e->section[0] != '\0' ? "." : "",
            e->key, kv_type_name(e->value.type), text);
    if (text != small)
        free(text);
    d->shown++;
    return 0;
}

static void usage(void)
{
    fputs("usage: kvdump [-f] [-t] [-q | -v] [-s section] file\n", stderr);
    exit(2);
}

int main(int argc, char **argv)
{
    struct dump d = { .out = stdout, .file = NULL, .shown = 0 };
    const char *section = NULL;
    kv_options opt;
    kv_stats stats;
    kv_doc doc;
    FILE *fp;
    int i, rc;

    kv_options_init(&opt);
    for (i = 1; i < argc && argv[i][0] == '-' && argv[i][1] != '\0'; i++) {
        if (strcmp(argv[i], "-f") == 0)
            opt.flags |= KV_OPT_CASE_FOLD;
        else if (strcmp(argv[i], "-t") == 0)
            opt.flags |= KV_OPT_NO_TYPES;
        else if (strcmp(argv[i], "-q") == 0)
            opt.log_level = KV_LOG_ERROR;
        else if (strcmp(argv[i], "-v") == 0)
            opt.log_level = KV_LOG_DEBUG;
        else if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
            section = argv[++i];
        else
            usage();
    }
    if (i != argc - 1)
        usage();
    d.file = argv[i];
    opt.log = to_stderr;
    opt.log_ctx = &d;

    fp = fopen(d.file, "rb");
    if (fp == NULL) {
        fprintf(stderr, "kvdump: %s: cannot open\n", d.file);
        return 1;
    }
    kv_doc_init(&doc);
    rc = kv_parse_file(&doc, fp, &opt);
    fclose(fp);
    if (rc != KV_OK) {
        fprintf(stderr, "kvdump: %s:%d:%d: %s\n", d.file, doc.error.line, doc.error.column,
                doc.error.message);
        kv_doc_free(&doc);
        return 1;
    }
    rc = kv_each(&doc, section, print_entry, &d);
    kv_doc_stats(&doc, &stats);
    printf("# %d of %lu entries, %lu sections, %lu strings\n", d.shown,
           (unsigned long)stats.entries, (unsigned long)stats.sections,
           (unsigned long)stats.by_type[KV_STR]);
    kv_doc_free(&doc);
    return rc == 0 ? 0 : 1;
}
