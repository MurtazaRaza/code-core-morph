#include <string.h>

#include "tap.h"
#include "wfint.h"

int main(void)
{
    sbuf b;
    size_t i;
    int all;

    sbuf_init(&b);
    ok(b.data == NULL && b.len == 0, "a new buffer is empty");

    ok(sbuf_append(&b, "abc", 3) == WF_OK, "append succeeds");
    ok(b.len == 3 && strcmp(b.data, "abc") == 0, "append keeps the text NUL-terminated");

    ok(sbuf_putc(&b, 'd') == WF_OK && strcmp(b.data, "abcd") == 0, "putc adds one byte");

    all = 1;
    for (i = 0; i < 1000; i++)
        if (sbuf_putc(&b, 'x') != WF_OK)
            all = 0;
    ok(all && b.len == 1004 && b.cap > 1004, "the buffer grows as needed");

    sbuf_reset(&b);
    ok(b.len == 0 && b.data[0] == '\0' && b.cap > 0, "reset empties but keeps the memory");

    ok(sbuf_append(&b, "", 0) == WF_OK && b.len == 0, "appending nothing is fine");

    sbuf_free(&b);
    ok(b.data == NULL && b.cap == 0, "free releases everything");
    return done();
}
