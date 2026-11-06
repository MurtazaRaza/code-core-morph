#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "tap.h"
#include "wfint.h"

static void sum_counts(const hentry *e, void *ctx)
{
    *(unsigned long *)ctx += e->count;
}

int main(void)
{
    htab h;
    unsigned long total;
    char key[16];
    int i;
    int all;

    ok(wf_hash("", 0) == 2166136261UL, "the hash of nothing is the FNV offset basis");
    ok(wf_hash("a", 1) == 0xe40c292cUL, "the FNV-1a hash of \"a\"");

    errno = 0;
    ok(htab_init(&h, 0) == WF_EINVAL && errno == EINVAL, "no buckets is a bad argument");

    ok(htab_init(&h, 4) == WF_OK && h.size == 0, "a new table is empty");
    ok(htab_add(&h, "cat", 3, 1) == WF_OK && htab_get(&h, "cat", 3) == 1, "add a word");
    ok(htab_add(&h, "cat", 3, 2) == WF_OK && htab_get(&h, "cat", 3) == 3 && h.size == 1,
       "adding again adds to its count");
    ok(htab_get(&h, "ca", 2) == 0 && htab_get(&h, "dog", 3) == 0, "missing words count 0");

    all = 1;
    for (i = 0; i < 100; i++) {
        sprintf(key, "w%d", i);
        if (htab_add(&h, key, strlen(key), 1) != WF_OK)
            all = 0;
    }
    ok(all && h.size == 101 && h.nbuckets > 4, "the table grows");
    ok(htab_get(&h, "w42", 3) == 1 && htab_get(&h, "cat", 3) == 3,
       "growing keeps every word");

    total = 0;
    htab_each(&h, sum_counts, &total);
    ok(total == 103, "each visits every word once");

    ok(htab_add(&h, "cat", 3, (unsigned long)-1) == WF_ERANGE
       && htab_get(&h, "cat", 3) == 3, "an overflowing count is refused");

    htab_free(&h);
    ok(h.buckets == NULL && h.size == 0, "free empties the table");
    return done();
}
