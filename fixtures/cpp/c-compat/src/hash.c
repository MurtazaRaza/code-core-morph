#include "ccore/hash.h"

#include <stdlib.h>
#include <string.h>

ccore_hash_t ccore_fnv1a(const void* bytes, size_t count) {
    const unsigned char* p = (const unsigned char*)bytes;
    ccore_hash_t hash = 2166136261u;
    size_t i;
    for (i = 0; i < count; ++i) {
        hash ^= p[i];
        hash *= 16777619u;
    }
    return hash;
}

ccore_hash_t ccore_fnv1a_str(const char* text) { return ccore_fnv1a(text, strlen(text)); }

int ccore_set_init(ccore_set* set, size_t capacity) {
    size_t slots = 8;
    while (slots < capacity * 2) {
        slots *= 2;
    }
    set->slots = (char**)calloc(slots, sizeof(char*));
    set->capacity = set->slots ? slots : 0;
    set->count = 0;
    return set->slots != NULL;
}

void ccore_set_free(ccore_set* set) {
    size_t i;
    for (i = 0; i < set->capacity; ++i) {
        free(set->slots[i]);
    }
    free(set->slots);
    set->slots = NULL;
    set->capacity = 0;
    set->count = 0;
}

static size_t find_slot(const ccore_set* set, const char* text) {
    size_t mask = set->capacity - 1;
    size_t i = ccore_fnv1a_str(text) & mask;
    while (set->slots[i] != NULL && strcmp(set->slots[i], text) != 0) {
        i = (i + 1) & mask;
    }
    return i;
}

int ccore_set_add(ccore_set* set, const char* text) {
    size_t slot;
    size_t length;
    if (set->capacity == 0 || (set->count + 1) * 2 > set->capacity) {
        return -1;
    }
    slot = find_slot(set, text);
    if (set->slots[slot] != NULL) {
        return 0;
    }
    length = strlen(text);
    set->slots[slot] = (char*)malloc(length + 1);
    if (set->slots[slot] == NULL) {
        return -1;
    }
    memcpy(set->slots[slot], text, length + 1);
    set->count++;
    return 1;
}

int ccore_set_contains(const ccore_set* set, const char* text) {
    if (set->capacity == 0) {
        return 0;
    }
    return set->slots[find_slot(set, text)] != NULL;
}
