#ifndef MM_UTIL_STRINGS_H
#define MM_UTIL_STRINGS_H

#include <string>
#include <vector>

namespace mm {
namespace util {

typedef std::vector<std::string> StringList;

std::string toLower(const std::string& text);
std::string trim(const std::string& text);
StringList split(const std::string& text, char separator);
std::string join(const StringList& parts, const std::string& separator);
bool startsWith(const std::string& text, const std::string& prefix);
std::string replaceAll(const std::string& text, const std::string& from, const std::string& to);

}  // namespace util
}  // namespace mm

#endif  // MM_UTIL_STRINGS_H
