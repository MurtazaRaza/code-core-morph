#ifndef CALC_STRUTIL_H
#define CALC_STRUTIL_H

#include <string>
#include <vector>

namespace calc {
namespace strutil {

// Removes spaces, tabs, carriage returns and newlines from both ends.
std::string trim(const std::string& s);

// Splits on every sep: "a,,b" gives "a", "", "b", and "" gives one empty field.
std::vector<std::string> split(const std::string& s, char sep);

std::string join(const std::vector<std::string>& parts, const std::string& sep);

// ASCII letters only; every other byte stays as it is.
std::string to_lower(const std::string& s);

bool starts_with(const std::string& s, const std::string& prefix);

// Replaces each occurrence of from (not empty), left to right, and returns how many.
int replace_all(std::string& s, const std::string& from, const std::string& to);

} // namespace strutil
} // namespace calc

#endif
