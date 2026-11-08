/* stats.c - descriptive statistics and a fixed-width histogram (from src/stats.rs). */
#include "stats/stats.h"

#include <math.h>
#include <stdlib.h>

stats_status stats_mean(const double *xs, size_t n, double *out)
{
    double sum = 0.0;
    size_t i;
    if (n == 0) {
        return STATS_EMPTY;
    }
    for (i = 0; i < n; i++) {
        sum += xs[i];
    }
    *out = sum / (double)n;
    return STATS_OK;
}

/* f64::total_cmp's order for the values a median meets: NaN after every number. */
static int compare(const void *a, const void *b)
{
    double x = *(const double *)a, y = *(const double *)b;
    if (isnan(x) || isnan(y)) {
        return isnan(x) - isnan(y);
    }
    return (x > y) - (x < y);
}

stats_status stats_median(double *xs, size_t n, double *out)
{
    size_t mid;
    if (n == 0) {
        return STATS_EMPTY;
    }
    qsort(xs, n, sizeof *xs, compare);
    mid = n / 2;
    *out = n % 2 == 0 ? (xs[mid - 1] + xs[mid]) / 2.0 : xs[mid];
    return STATS_OK;
}

struct stats_histogram {
    double lo;
    double hi;
    size_t bins;
    uint32_t *counts;
};

stats_histogram *stats_histogram_new(size_t bins, double lo, double hi)
{
    stats_histogram *h;
    if (bins == 0 || !(lo < hi)) {
        return NULL;
    }
    h = malloc(sizeof *h);
    if (h == NULL) {
        return NULL;
    }
    h->counts = calloc(bins, sizeof *h->counts);
    if (h->counts == NULL) {
        free(h);
        return NULL;
    }
    h->lo = lo;
    h->hi = hi;
    h->bins = bins;
    return h;
}

void stats_histogram_free(stats_histogram *h)
{
    if (h != NULL) {
        free(h->counts);
        free(h);
    }
}

stats_status stats_histogram_add(stats_histogram *h, double x)
{
    double width;
    size_t i;
    if (!(x >= h->lo && x < h->hi)) {
        return STATS_OUT_OF_RANGE;
    }
    width = (h->hi - h->lo) / (double)h->bins;
    i = (size_t)((x - h->lo) / width);
    if (i > h->bins - 1) {
        i = h->bins - 1;
    }
    h->counts[i]++;
    return STATS_OK;
}

bool stats_histogram_count(const stats_histogram *h, size_t bin, uint32_t *out)
{
    if (bin >= h->bins) {
        return false;
    }
    *out = h->counts[bin];
    return true;
}

size_t stats_histogram_bins(const stats_histogram *h)
{
    return h->bins;
}
