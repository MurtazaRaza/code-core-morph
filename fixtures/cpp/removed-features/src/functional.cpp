#include "toolkit/functional.h"

#include <algorithm>
#include <cctype>
#include <iterator>

namespace toolkit {

namespace {
bool isVowel(char c) {
    char lower = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return lower == 'a' || lower == 'e' || lower == 'i' || lower == 'o' || lower == 'u';
}
}  // namespace

std::vector<int> scaled(const std::vector<int>& values, int factor) {
    std::vector<int> out;
    std::transform(values.begin(), values.end(), std::back_inserter(out),
                   std::bind2nd(std::multiplies<int>(), factor));
    return out;
}

std::vector<int> offset(const std::vector<int>& values, int amount) {
    std::vector<int> out;
    std::transform(values.begin(), values.end(), std::back_inserter(out),
                   std::bind1st(std::plus<int>(), amount));
    return out;
}

int countOdd(const std::vector<int>& values) {
    return static_cast<int>(std::count_if(values.begin(), values.end(), std::not1(IsEven())));
}

int countBelow(const std::vector<int>& values, int limit) {
    return static_cast<int>(
        std::count_if(values.begin(), values.end(), std::bind2nd(InRange(), limit)));
}

std::string vowels(const std::string& text) {
    std::string out;
    std::remove_copy_if(text.begin(), text.end(), std::back_inserter(out),
                        std::not1(std::ptr_fun(isVowel)));
    return out;
}

std::vector<std::string> names(const std::vector<Entry>& entries) {
    std::vector<std::string> out;
    std::transform(entries.begin(), entries.end(), std::back_inserter(out),
                   std::mem_fun_ref(&Entry::name));
    return out;
}

}  // namespace toolkit
