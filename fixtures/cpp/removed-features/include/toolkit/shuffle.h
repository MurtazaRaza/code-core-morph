#ifndef TOOLKIT_SHUFFLE_H
#define TOOLKIT_SHUFFLE_H

#include <cstddef>
#include <vector>

namespace toolkit {

// A tiny deterministic generator usable with std::random_shuffle: rng(n) returns 0..n-1.
class Lcg {
public:
    explicit Lcg(unsigned long seed) : state_(seed) {}
    std::ptrdiff_t operator()(std::ptrdiff_t n);

private:
    unsigned long state_;
};

std::vector<int> shuffled(const std::vector<int>& values, unsigned long seed);
std::vector<int> deal(int cards, int hands, unsigned long seed);

// Sum with a register variable, the way hot loops were written once.
long fastSum(const int* values, int count);

}  // namespace toolkit

#endif  // TOOLKIT_SHUFFLE_H
