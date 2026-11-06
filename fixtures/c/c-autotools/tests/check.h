/*
 * check.h - the tests' checks. CHECK(cond) counts a check and reports one
 * that fails; check_done() prints the count and gives the exit status
 * automake's test harness reads (0 passes).
 */
#ifndef CHECK_H
#define CHECK_H

#include <stdio.h>
#include <string.h>

static int checks_run = 0;
static int checks_failed = 0;

#define CHECK(cond)                                                       \
    do {                                                                  \
        checks_run++;                                                     \
        if (!(cond)) {                                                    \
            checks_failed++;                                              \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__,        \
                    __LINE__, #cond);                                     \
        }                                                                 \
    } while (0)

#define CHECK_STR(a, b) CHECK(strcmp((a), (b)) == 0)

static int check_done(const char *name)
{
    printf("%s: %d checks, %d failed\n", name, checks_run, checks_failed);
    return checks_failed != 0;
}

#endif /* CHECK_H */
