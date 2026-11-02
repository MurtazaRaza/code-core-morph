/* A growable byte buffer. Shared between C and C++: keep it valid C. */
#ifndef CCORE_BUFFER_H
#define CCORE_BUFFER_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ccore_buffer {
    unsigned char* data;
    size_t size;
    size_t capacity;
} ccore_buffer;

typedef enum ccore_status {
    CCORE_OK = 0,
    CCORE_NO_MEMORY = 1,
    CCORE_OUT_OF_RANGE = 2
} ccore_status;

void ccore_buffer_init(ccore_buffer* buffer);
void ccore_buffer_free(ccore_buffer* buffer);
ccore_status ccore_buffer_append(ccore_buffer* buffer, const void* bytes, size_t count);
ccore_status ccore_buffer_append_str(ccore_buffer* buffer, const char* text);
ccore_status ccore_buffer_erase(ccore_buffer* buffer, size_t offset, size_t count);
void ccore_buffer_clear(ccore_buffer* buffer);

#ifdef __cplusplus
}
#endif

#endif /* CCORE_BUFFER_H */
