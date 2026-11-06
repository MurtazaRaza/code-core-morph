/*
 * kvint.h - what the library's files share and the API doesn't show.
 */
#ifndef KVINT_H
#define KVINT_H

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#include <stdlib.h>
#include <string.h>

#include "kvconf.h"

#define KVI_COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define KVI_IS_SPACE(c) ((c) == ' ' || (c) == '\t')

/* strutil.c */
char *kvi_strndup(const char *s, size_t len);
int kvi_casecmp(const char *a, const char *b);
int kvi_casencmp(const char *a, const char *b, size_t len);
size_t kvi_trim(const char **s, size_t len);
void kvi_lower(char *s);
int kvi_valid_name(const char *s, size_t len);
int kvi_copy_name(char *dst, const char *src, size_t len, unsigned flags);

#endif /* KVINT_H */
