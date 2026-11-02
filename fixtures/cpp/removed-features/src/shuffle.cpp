#include "toolkit/shuffle.h"

#include <algorithm>

namespace toolkit {

std::ptrdiff_t Lcg::operator()(std::ptrdiff_t n) {
    state_ = (state_ * 1103515245UL + 12345UL) & 0x7fffffffUL;
    return static_cast<std::ptrdiff_t>(state_ % static_cast<unsigned long>(n));
}

std::vector<int> shuffled(const std::vector<int>& values, unsigned long seed) {
    std::vector<int> out(values);
    Lcg rng(seed);
    std::random_shuffle(out.begin(), out.end(), rng);
    return out;
}

std::vector<int> deal(int cards, int hands, unsigned long seed) {
    std::vector<int> deck;
    for (int i = 0; i < cards; ++i) {
        deck.push_back(i);
    }
    deck = shuffled(deck, seed);
    std::vector<int> totals(hands, 0);
    for (int i = 0; i < cards; ++i) {
        totals[i % hands] += deck[i];
    }
    return totals;
}

long fastSum(const int* values, int count) {
    register long total = 0;
    for (register int i = 0; i < count; ++i) {
        total += values[i];
    }
    return total;
}

}  // namespace toolkit
