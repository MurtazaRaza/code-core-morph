#ifndef TEXTKIT_REPORT_H
#define TEXTKIT_REPORT_H

#include <string>
#include <vector>

#include "textkit/ring.h"

namespace textkit {

// "len=2 cap=4 sum=7"; "(none)" for a null ring.
std::string describe(const tk_ring* ring);

// Fills a ring of the given capacity with the values, dropping those that don't fit, and
// returns how many were dropped.
size_t fill(tk_ring* ring, const std::vector<int32_t>& values);

}  // namespace textkit

#endif  // TEXTKIT_REPORT_H
