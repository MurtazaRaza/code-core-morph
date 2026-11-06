#include <string.h>

#include "tap.h"
#include "wfint.h"

/* The words of text, joined by "|", into out. */
static const char *words(const char *text, char *out)
{
    tokenizer t;
    sbuf w;
    int found;

    out[0] = '\0';
    sbuf_init(&w);
    tok_init(&t, text, strlen(text));
    while (tok_next(&t, &w, &found) == WF_OK && found) {
        if (out[0] != '\0')
            strcat(out, "|");
        strcat(out, w.data);
    }
    sbuf_free(&w);
    return out;
}

int main(void)
{
    char out[256];

    wf_set_fold_case(1);
    wf_set_min_length(1);
    ok(strcmp(words("The cat, the HAT.", out), "the|cat|the|hat") == 0,
       "words are split at punctuation and folded");
    ok(strcmp(words("  \t\n ", out), "") == 0, "blank text has no words");
    ok(strcmp(words("don't stop 'til", out), "don't|stop|til") == 0,
       "an apostrophe inside a word belongs to it");
    ok(strcmp(words("route 66, 3rd", out), "route|66|3rd") == 0, "digits are word characters");

    wf_set_fold_case(0);
    ok(strcmp(words("The Cat", out), "The|Cat") == 0, "case is kept when folding is off");
    wf_set_fold_case(1);

    wf_set_min_length(3);
    ok(strcmp(words("a an the cats", out), "the|cats") == 0, "short words are skipped");
    ok(wf_min_length() == 3 && wf_fold_case() == 1, "the settings read back");
    wf_set_min_length(1);
    return done();
}
