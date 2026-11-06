/*
 * wf.h - word frequency counting, the public interface.
 *
 * Every function that can fail returns a wf_status. Bad arguments also
 * set errno to EINVAL.
 */
#ifndef WF_H
#define WF_H

#include <stddef.h>

typedef enum wf_status {
    WF_OK = 0,
    WF_ENOMEM = 1,  /* out of memory */
    WF_EINVAL = 2,  /* a bad argument */
    WF_ERANGE = 3   /* a count would overflow */
} wf_status;

/* One counted word. text is NUL-terminated and belongs to the table. */
typedef struct wf_word {
    const char *text;
    size_t len;
    unsigned long count;
} wf_word;

typedef struct wf_table wf_table;

wf_status wf_table_new(wf_table **out);
void wf_table_free(wf_table *table);

/* Count every word of text (len bytes; it need not be NUL-terminated). */
wf_status wf_count_text(wf_table *table, const char *text, size_t len);

size_t wf_table_size(const wf_table *table);
unsigned long wf_lookup(const wf_table *table, const char *word);

/*
 * The n most frequent words, most frequent first, ties in byte order.
 * Returns how many were written to out (fewer when the table is smaller,
 * 0 with errno ENOMEM when memory runs out).
 */
size_t wf_top(const wf_table *table, wf_word *out, size_t n);

const char *wf_strerror(wf_status status);

/* Settings for the words counted from now on (all tables). */
void wf_set_min_length(size_t min);
size_t wf_min_length(void);
void wf_set_fold_case(int fold);
int wf_fold_case(void);

#endif
