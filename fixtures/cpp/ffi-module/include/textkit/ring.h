#ifndef TEXTKIT_RING_H
#define TEXTKIT_RING_H

/* The textkit C API: a fixed-capacity ring buffer of 32-bit integers, and a checksum. */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct tk_ring tk_ring;

/* A new, empty ring that holds up to `capacity` values; NULL when capacity is 0. */
tk_ring* tk_ring_new(size_t capacity);

/* Frees a ring; NULL is allowed. */
void tk_ring_free(tk_ring* ring);

/* Appends a value: 1 if it was stored, 0 if the ring is full. */
int tk_ring_push(tk_ring* ring, int32_t value);

/* Takes the oldest value into *out: 1 if there was one, 0 if the ring is empty. */
int tk_ring_pop(tk_ring* ring, int32_t* out);

size_t tk_ring_len(const tk_ring* ring);
size_t tk_ring_capacity(const tk_ring* ring);

/* The sum of the values in the ring, without overflow for up to 2^32 values. */
int64_t tk_ring_sum(const tk_ring* ring);

/* The Adler-32 checksum of `len` bytes. */
uint32_t tk_checksum(const uint8_t* data, size_t len);

#ifdef __cplusplus
}
#endif

#endif /* TEXTKIT_RING_H */
