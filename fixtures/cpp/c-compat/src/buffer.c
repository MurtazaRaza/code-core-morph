#include "ccore/buffer.h"

#include <stdlib.h>
#include <string.h>

void ccore_buffer_init(ccore_buffer* buffer) {
    buffer->data = NULL;
    buffer->size = 0;
    buffer->capacity = 0;
}

void ccore_buffer_free(ccore_buffer* buffer) {
    free(buffer->data);
    ccore_buffer_init(buffer);
}

static ccore_status reserve(ccore_buffer* buffer, size_t needed) {
    size_t capacity;
    unsigned char* data;
    if (needed <= buffer->capacity) {
        return CCORE_OK;
    }
    capacity = buffer->capacity ? buffer->capacity : 16;
    while (capacity < needed) {
        capacity *= 2;
    }
    data = (unsigned char*)realloc(buffer->data, capacity);
    if (data == NULL) {
        return CCORE_NO_MEMORY;
    }
    buffer->data = data;
    buffer->capacity = capacity;
    return CCORE_OK;
}

ccore_status ccore_buffer_append(ccore_buffer* buffer, const void* bytes, size_t count) {
    ccore_status status = reserve(buffer, buffer->size + count);
    if (status != CCORE_OK) {
        return status;
    }
    if (count > 0) {
        memcpy(buffer->data + buffer->size, bytes, count);
    }
    buffer->size += count;
    return CCORE_OK;
}

ccore_status ccore_buffer_append_str(ccore_buffer* buffer, const char* text) {
    return ccore_buffer_append(buffer, text, strlen(text));
}

ccore_status ccore_buffer_erase(ccore_buffer* buffer, size_t offset, size_t count) {
    if (offset > buffer->size || count > buffer->size - offset) {
        return CCORE_OUT_OF_RANGE;
    }
    memmove(buffer->data + offset, buffer->data + offset + count, buffer->size - offset - count);
    buffer->size -= count;
    return CCORE_OK;
}

void ccore_buffer_clear(ccore_buffer* buffer) { buffer->size = 0; }
