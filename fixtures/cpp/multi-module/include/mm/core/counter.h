#ifndef MM_CORE_COUNTER_H
#define MM_CORE_COUNTER_H

#include <map>
#include <string>
#include <utility>
#include <vector>

#include "mm/core/document.h"

namespace mm {
namespace core {

typedef std::map<std::string, int> Counts;
typedef std::pair<std::string, int> Entry;
typedef std::vector<Entry> Ranking;

class WordCounter {
public:
    WordCounter();

    void add(const Document& document);
    void addWord(const std::string& word);
    int count(const std::string& word) const;
    int total() const { return total_; }
    int distinct() const { return static_cast<int>(counts_.size()); }
    Ranking top(int n) const;  // most frequent first, ties alphabetical

private:
    Counts counts_;
    int total_;
};

}  // namespace core
}  // namespace mm

#endif  // MM_CORE_COUNTER_H
