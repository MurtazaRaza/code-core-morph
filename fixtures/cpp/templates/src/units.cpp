#include "meta/units.h"

#include <cstdio>

namespace meta {

std::string formatMeasure(double value, const char* unit) {
    char buffer[64];
    std::snprintf(buffer, sizeof(buffer), "%.2f %s", value, unit);
    return std::string(buffer);
}

}  // namespace meta
