/*
 * tok.c - splits text into words: runs of letters and digits, with an
 * apostrophe allowed between two letters ("don't"). Words shorter than
 * the minimum length are skipped; letters are folded to lower case when
 * the settings say so.
 */
#include <ctype.h>

#include "wfint.h"

void tok_init(tokenizer *t, const char *text, size_t len)
{
    t->p = text;
    t->end = text + len;
}

static int is_word_char(int c)
{
    return isalnum(c);
}

wf_status tok_next(tokenizer *t, sbuf *word, int *found)
{
    wf_status st;
    int c;

    for (;;) {
        while (t->p < t->end && !is_word_char((unsigned char)*t->p))
            t->p++;
        if (t->p == t->end) {
            *found = 0;
            return WF_OK;
        }
        sbuf_reset(word);
        while (t->p < t->end) {
            c = (unsigned char)*t->p;
            if (c == '\'' && word->len > 0 && t->p + 1 < t->end
                && isalpha((unsigned char)t->p[1])
                && isalpha((unsigned char)word->data[word->len - 1])) {
                /* an apostrophe inside a word */
            } else if (!is_word_char(c)) {
                break;
            }
            if (wf_conf_fold_case)
                c = tolower(c);
            st = sbuf_putc(word, c);
            if (st != WF_OK)
                return st;
            t->p++;
        }
        if (word->len >= wf_conf_min_length) {
            *found = 1;
            return WF_OK;
        }
    }
}
