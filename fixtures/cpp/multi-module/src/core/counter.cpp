#include "mm/core/counter.h"

#include <algorithm>

namespace mm {
namespace core {

namespace {
bool byCountThenName(const Entry& a, const Entry& b) {
    if (a.second != b.second) {
        return a.second > b.second;
    }
    return a.first < b.first;
}
}  // namespace

WordCounter::WordCounter() : total_(0) {}

void WordCounter::add(const Document& document) {
    util::StringList words = document.words();
    for (util::StringList::const_iterator it = words.begin(); it != words.end(); ++it) {
        addWord(*it);
    }
}

void WordCounter::addWord(const std::string& word) {
    if (word.empty()) {
        return;
    }
    ++counts_[word];
    ++total_;
}

int WordCounter::count(const std::string& word) const {
    Counts::const_iterator it = counts_.find(word);
    return it == counts_.end() ? 0 : it->second;
}

Ranking WordCounter::top(int n) const {
    Ranking ranking(counts_.begin(), counts_.end());
    std::sort(ranking.begin(), ranking.end(), byCountThenName);
    if (n >= 0 && static_cast<Ranking::size_type>(n) < ranking.size()) {
        ranking.resize(static_cast<Ranking::size_type>(n));
    }
    return ranking;
}

}  // namespace core
}  // namespace mm
