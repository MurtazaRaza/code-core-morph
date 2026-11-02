/* FNV-1a hashing and a small open-addressing hash set of strings. Valid C. */
#ifndef CCORE_HASH_H
#define CCORE_HASH_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uint32_t ccore_hash_t;

ccore_hash_t ccore_fnv1a(const void* bytes, size_t count);
ccore_hash_t ccore_fnv1a_str(const char* text);

typedef struct ccore_set {
    char** slots;
    size_t capacity;
    size_t count;
} ccore_set;

int ccore_set_init(ccore_set* set, size_t capacity);
void ccore_set_free(ccore_set* set);
int ccore_set_add(ccore_set* set, const char* text);   /* 1 if added, 0 if present, -1 on error */
int ccore_set_contains(const ccore_set* set, const char* text);

#ifdef __cplusplus
}
#endif

#endif /* CCORE_HASH_H */
