#include "textkit/ring.h"

#include <new>
#include <vector>

struct tk_ring {
    std::vector<int32_t> slots;
    size_t head;  // index of the oldest value
    size_t count;
};

extern "C" {

tk_ring* tk_ring_new(size_t capacity) {
    if (capacity == 0) {
        return nullptr;
    }
    tk_ring* ring = new (std::nothrow) tk_ring;
    if (ring == nullptr) {
        return nullptr;
    }
    ring->slots.assign(capacity, 0);
    ring->head = 0;
    ring->count = 0;
    return ring;
}

void tk_ring_free(tk_ring* ring) {
    delete ring;
}

int tk_ring_push(tk_ring* ring, int32_t value) {
    if (ring->count == ring->slots.size()) {
        return 0;
    }
    size_t tail = (ring->head + ring->count) % ring->slots.size();
    ring->slots[tail] = value;
    ++ring->count;
    return 1;
}

int tk_ring_pop(tk_ring* ring, int32_t* out) {
    if (ring->count == 0) {
        return 0;
    }
    *out = ring->slots[ring->head];
    ring->head = (ring->head + 1) % ring->slots.size();
    --ring->count;
    return 1;
}

size_t tk_ring_len(const tk_ring* ring) {
    return ring->count;
}

size_t tk_ring_capacity(const tk_ring* ring) {
    return ring->slots.size();
}

int64_t tk_ring_sum(const tk_ring* ring) {
    int64_t sum = 0;
    for (size_t i = 0; i < ring->count; ++i) {
        sum += ring->slots[(ring->head + i) % ring->slots.size()];
    }
    return sum;
}

uint32_t tk_checksum(const uint8_t* data, size_t len) {
    const uint32_t mod = 65521;
    uint32_t a = 1;
    uint32_t b = 0;
    for (size_t i = 0; i < len; ++i) {
        a = (a + data[i]) % mod;
        b = (b + a) % mod;
    }
    return (b << 16) | a;
}

}  // extern "C"
