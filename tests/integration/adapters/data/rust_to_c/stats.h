/* stats/stats.h - the module stats::stats, ported to C99 (a hand port, D-75). */
#ifndef STATS_STATS_H
#define STATS_STATS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum stats_status {
    STATS_OK = 0,
    STATS_EMPTY = 1,        /* no values */
    STATS_OUT_OF_RANGE = 2  /* a value outside the histogram's range */
} stats_status;

/* The arithmetic mean of xs[0..n) into *out; STATS_EMPTY when n is 0. */
stats_status stats_mean(const double *xs, size_t n, double *out);

/* The median of xs[0..n) into *out, sorting xs in place; STATS_EMPTY when n is 0. */
stats_status stats_median(double *xs, size_t n, double *out);

/* Counts of values in equal-width bins over [lo, hi). */
typedef struct stats_histogram stats_histogram;

/* NULL when bins is 0, the range is empty, or memory runs out. */
stats_histogram *stats_histogram_new(size_t bins, double lo, double hi);
void stats_histogram_free(stats_histogram *h);
stats_status stats_histogram_add(stats_histogram *h, double x);
/* false when bin is past the last bin. */
bool stats_histogram_count(const stats_histogram *h, size_t bin, uint32_t *out);
size_t stats_histogram_bins(const stats_histogram *h);

#endif
