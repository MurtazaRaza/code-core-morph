#include "textkit/report.h"

#include <sstream>

namespace textkit {

std::string describe(const tk_ring* ring) {
    if (ring == nullptr) {
        return "(none)";
    }
    std::ostringstream out;
    out << "len=" << tk_ring_len(ring) << " cap=" << tk_ring_capacity(ring)
        << " sum=" << tk_ring_sum(ring);
    return out.str();
}

size_t fill(tk_ring* ring, const std::vector<int32_t>& values) {
    size_t dropped = 0;
    for (int32_t value : values) {
        if (!tk_ring_push(ring, value)) {
            ++dropped;
        }
    }
    return dropped;
}

}  // namespace textkit
