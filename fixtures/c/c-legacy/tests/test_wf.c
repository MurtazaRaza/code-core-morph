#include <errno.h>
#include <string.h>

#include "tap.h"
#include "wf.h"

int main(void)
{
    static const char text[] = "b a c b a b, A! c d";
    wf_table *table;
    wf_word top[8];
    size_t n;

    errno = 0;
    ok(wf_table_new(NULL) == WF_EINVAL && errno == EINVAL, "a table needs somewhere to go");
    ok(wf_table_new(&table) == WF_OK && wf_table_size(table) == 0, "a new table is empty");

    ok(wf_count_text(table, text, sizeof(text) - 1) == WF_OK, "counting succeeds");
    ok(wf_table_size(table) == 4, "four different words");
    ok(wf_lookup(table, "a") == 3 && wf_lookup(table, "b") == 3 && wf_lookup(table, "z") == 0,
       "lookup counts folded words");

    n = wf_top(table, top, 3);
    ok(n == 3, "top returns as many as asked");
    ok(strcmp(top[0].text, "a") == 0 && top[0].count == 3 && strcmp(top[1].text, "b") == 0,
       "ties are in byte order");
    ok(strcmp(top[2].text, "c") == 0 && top[2].count == 2 && top[2].len == 1,
       "then the next most frequent");
    ok(wf_top(table, top, 8) == 4, "top returns no more than the table has");

    ok(wf_count_text(table, "x", 0) == WF_OK && wf_table_size(table) == 4,
       "counting nothing changes nothing");
    errno = 0;
    ok(wf_count_text(table, NULL, 3) == WF_EINVAL && errno == EINVAL, "NULL text is refused");

    ok(strcmp(wf_strerror(WF_ENOMEM), "out of memory") == 0
       && strcmp(wf_strerror((wf_status)99), "unknown error") == 0, "error messages");

    wf_table_free(table);
    wf_table_free(NULL);
    ok(1, "freeing NULL is fine");
    return done();
}
