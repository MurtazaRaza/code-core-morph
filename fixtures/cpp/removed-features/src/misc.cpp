#include "toolkit/misc.h"

#include <algorithm>

namespace toolkit {

bool concept(const std::string& word) {
    // A "concept" here is any capitalized word.
    return !word.empty() && word[0] >= 'A' && word[0] <= 'Z';
}

int requires(int level) {
    int requires = level * 2;
    return requires + 1;
}

const char* greeting() {
    const char* text = u8"hello, world";
    return text;
}

Range widen(Range range, int by) {
    Range wider = {range.low - by, range.high + by};
    return wider;
}

int span(const Range& range) { return range.high - range.low; }

void Counter::addAll(const std::vector<int>& values) {
    std::for_each(values.begin(), values.end(), [=](int value) { total_ += value; });
}

}  // namespace toolkit
