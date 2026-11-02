#ifndef TOOLKIT_MISC_H
#define TOOLKIT_MISC_H

#include <string>
#include <vector>

namespace toolkit {

// "concept" and "requires" became keywords in C++20.
bool concept(const std::string& word);
int requires(int level);

// A u8 literal is const char* before C++20 and const char8_t* after.
const char* greeting();

// An aggregate with a defaulted constructor: aggregate initialization works until C++20.
struct Range {
    Range() = default;
    int low;
    int high;
};

Range widen(Range range, int by);
int span(const Range& range);

// operator== as a non-const member: ambiguous under C++20's rewritten comparisons.
struct Version {
    int major;
    int minor;
    bool operator==(const Version& other) { return major == other.major && minor == other.minor; }
    bool operator!=(const Version& other) { return !(*this == other); }
};

// A counter whose callbacks capture this implicitly through [=].
class Counter {
public:
    Counter() : total_(0) {}
    void addAll(const std::vector<int>& values);
    int total() const { return total_; }

private:
    int total_;
};

}  // namespace toolkit

#endif  // TOOLKIT_MISC_H
