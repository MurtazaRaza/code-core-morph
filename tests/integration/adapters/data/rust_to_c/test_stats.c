/* The stats module's tests (src/stats.rs, mod tests), translated from Rust. */
#include <math.h>
#include <stddef.h>

#include "codecoremorph_tap.h"
#include "stats/stats.h"

static void mean_of_three(void)
{
    double xs[] = {1.0, 2.0, 6.0};
    double out = 0.0;
    CCM_CHECK_INT(stats_mean(xs, 3, &out), STATS_OK);
    CCM_CHECK_NEAR(out, 3.0, 0.0);
    CCM_CHECK_INT(stats_mean(NULL, 0, &out), STATS_EMPTY);
}

static void median_even_and_odd(void)
{
    double odd[] = {3.0, 1.0, 2.0};
    double even[] = {4.0, 1.0, 3.0, 2.0};
    double out = 0.0;
    CCM_CHECK_INT(stats_median(odd, 3, &out), STATS_OK);
    CCM_CHECK_NEAR(out, 2.0, 0.0);
    CCM_CHECK_INT(stats_median(even, 4, &out), STATS_OK);
    CCM_CHECK_NEAR(out, 2.5, 0.0);
}

static void histogram_bins(void)
{
    double xs[] = {0.5, 1.9, 2.0, 7.99};
    size_t i;
    uint32_t c0 = 0, c1 = 0, c3 = 0;
    stats_histogram *h = stats_histogram_new(4, 0.0, 8.0);
    CCM_CHECK(h != NULL);
    if (h == NULL) {
        return;
    }
    for (i = 0; i < 4; i++) {
        CCM_CHECK_INT(stats_histogram_add(h, xs[i]), STATS_OK);
    }
    CCM_CHECK(stats_histogram_count(h, 0, &c0));
    CCM_CHECK(stats_histogram_count(h, 1, &c1));
    CCM_CHECK(stats_histogram_count(h, 3, &c3));
    CCM_CHECK_UINT(c0, 2);
    CCM_CHECK_UINT(c1, 1);
    CCM_CHECK_UINT(c3, 1);
    CCM_CHECK_INT(stats_histogram_add(h, 8.0), STATS_OUT_OF_RANGE);
    CCM_CHECK(stats_histogram_new(0, 0.0, 1.0) == NULL);
    stats_histogram_free(h);
}

int main(int argc, char **argv)
{
    CCM_SELECT(argc, argv);
    CCM_RUN(mean_of_three);
    CCM_RUN(median_even_and_odd);
    CCM_RUN(histogram_bins);
    return ccm_done();
}
