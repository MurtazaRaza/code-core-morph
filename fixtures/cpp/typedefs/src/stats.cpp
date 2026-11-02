#include "geo/stats.h"

namespace geo {

namespace {
typedef ScalarList::size_type Size;
}  // namespace

ScalarList sorted(const ScalarList& values, ScalarLess less) {
    ScalarList out(values);
    std::sort(out.begin(), out.end(), less);
    return out;
}

Scalar median(const ScalarList& values) {
    if (values.empty()) {
        return 0.0;
    }
    ScalarList s = sorted(values, ascending);
    Size mid = s.size() / 2;
    if (s.size() % 2 == 1) {
        return s[mid];
    }
    return (s[mid - 1] + s[mid]) / 2.0;
}

void visitAll(const ScalarList& values, ScalarVisitor* visitor, void* context) {
    for (Size i = 0; i < values.size(); ++i) {
        visitor(values[i], context);
    }
}

}  // namespace geo
