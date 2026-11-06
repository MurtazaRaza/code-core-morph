/*
 * tap.h - a minimal TAP producer for the tests: ok(cond, "what") prints
 * "ok N - what" or "not ok N - what", and done() prints the plan and
 * returns the exit status.
 */
#ifndef TAP_H
#define TAP_H

#include <stdio.h>

static int tap_count = 0;
static int tap_failed = 0;

static void tap_ok(int pass, const char *what, const char *file, int line)
{
    tap_count++;
    if (pass) {
        printf("ok %d - %s\n", tap_count, what);
    } else {
        tap_failed++;
        printf("not ok %d - %s\n#   at %s line %d\n", tap_count, what, file, line);
    }
}

#define ok(cond, what) tap_ok((cond) != 0, (what), __FILE__, __LINE__)

static int done(void)
{
    printf("1..%d\n", tap_count);
    return tap_failed ? 1 : 0;
}

#endif
