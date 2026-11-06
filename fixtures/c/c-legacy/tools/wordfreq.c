/*
 * wordfreq - prints the most frequent words of its standard input.
 *
 * usage: wordfreq [-k] [-m min] [count]
 *   -k      keep case (by default words are folded to lower case)
 *   -m min  skip words shorter than min
 *   count   how many words to print (default 10)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wf.h"

static void usage(void)
{
    fprintf(stderr, "usage: wordfreq [-k] [-m min] [count]\n");
    exit(2);
}

static char *read_all(FILE *in, size_t *len)
{
    char *data;
    char *bigger;
    size_t cap;
    size_t n;

    cap = 4096;
    *len = 0;
    data = (char *)malloc(cap);
    if (data == NULL)
        return NULL;
    for (;;) {
        n = fread(data + *len, 1, cap - *len, in);
        *len += n;
        if (*len < cap)
            break;
        cap *= 2;
        bigger = (char *)realloc(data, cap);
        if (bigger == NULL) {
            free(data);
            return NULL;
        }
        data = bigger;
    }
    return data;
}

int main(int argc, char **argv)
{
    wf_table *table;
    wf_word *top;
    wf_status st;
    char *text;
    size_t len;
    size_t count;
    size_t n;
    size_t i;
    int arg;

    count = 10;
    for (arg = 1; arg < argc; arg++) {
        if (strcmp(argv[arg], "-k") == 0) {
            wf_set_fold_case(0);
        } else if (strcmp(argv[arg], "-m") == 0) {
            if (++arg == argc)
                usage();
            wf_set_min_length((size_t)atol(argv[arg]));
        } else if (argv[arg][0] == '-') {
            usage();
        } else {
            count = (size_t)atol(argv[arg]);
        }
    }

    text = read_all(stdin, &len);
    if (text == NULL) {
        fprintf(stderr, "wordfreq: out of memory\n");
        return 1;
    }
    st = wf_table_new(&table);
    if (st == WF_OK)
        st = wf_count_text(table, text, len);
    free(text);
    if (st != WF_OK) {
        fprintf(stderr, "wordfreq: %s\n", wf_strerror(st));
        return 1;
    }
    top = (wf_word *)malloc((count ? count : 1) * sizeof(wf_word));
    if (top == NULL) {
        fprintf(stderr, "wordfreq: out of memory\n");
        return 1;
    }
    n = wf_top(table, top, count);
    for (i = 0; i < n; i++)
        printf("%7lu %s\n", top[i].count, top[i].text);
    free(top);
    wf_table_free(table);
    return 0;
}
