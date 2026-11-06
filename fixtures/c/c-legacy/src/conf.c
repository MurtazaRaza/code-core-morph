/*
 * conf.c - the library's settings: plain globals, read by the tokenizer.
 */
#include "wfint.h"

size_t wf_conf_min_length = 1;
int wf_conf_fold_case = 1;

void wf_set_min_length(size_t min)
{
    wf_conf_min_length = min;
}

size_t wf_min_length(void)
{
    return wf_conf_min_length;
}

void wf_set_fold_case(int fold)
{
    wf_conf_fold_case = fold != 0;
}

int wf_fold_case(void)
{
    return wf_conf_fold_case;
}
