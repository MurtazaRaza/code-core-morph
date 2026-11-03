#include "billing/meter.h"

namespace billing {

Meter::Meter(Reading start) : start_(start), last_(start), count_(0), rolledOver_(false) {}

void Meter::record(Reading value) {
    if (value < last_) {
        rolledOver_ = true;
        return;
    }
    last_ = value;
    ++count_;
}

Reading Meter::consumption() const {
    return last_ - start_;
}

unsigned int Meter::readings() const {
    return count_;
}

bool Meter::rolledOver() const {
    return rolledOver_;
}

}  // namespace billing
