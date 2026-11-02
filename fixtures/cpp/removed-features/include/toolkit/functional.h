#ifndef TOOLKIT_FUNCTIONAL_H
#define TOOLKIT_FUNCTIONAL_H

#include <functional>
#include <string>
#include <vector>

namespace toolkit {

// Function objects in the C++98 style: derived from unary_function and binary_function.
struct IsEven : public std::unary_function<int, bool> {
    bool operator()(int value) const { return value % 2 == 0; }
};

struct InRange : public std::binary_function<int, int, bool> {
    bool operator()(int value, int limit) const { return value >= 0 && value < limit; }
};

struct Entry {
    std::string key;
    int weight;
    const std::string& name() const { return key; }
};

std::vector<int> scaled(const std::vector<int>& values, int factor);  // bind2nd
std::vector<int> offset(const std::vector<int>& values, int amount);  // bind1st
int countOdd(const std::vector<int>& values);                         // not1
int countBelow(const std::vector<int>& values, int limit);            // bind2nd + binary_function
std::string vowels(const std::string& text);                          // ptr_fun
std::vector<std::string> names(const std::vector<Entry>& entries);    // mem_fun_ref

}  // namespace toolkit

#endif  // TOOLKIT_FUNCTIONAL_H
