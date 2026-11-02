#include "mm/util/strings.h"

#include <cctype>

namespace mm {
namespace util {

std::string toLower(const std::string& text) {
    std::string out(text);
    for (std::string::size_type i = 0; i < out.size(); ++i) {
        out[i] = static_cast<char>(std::tolower(static_cast<unsigned char>(out[i])));
    }
    return out;
}

std::string trim(const std::string& text) {
    std::string::size_type first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return std::string();
    }
    std::string::size_type last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

StringList split(const std::string& text, char separator) {
    StringList parts;
    std::string::size_type start = 0;
    while (true) {
        std::string::size_type end = text.find(separator, start);
        if (end == std::string::npos) {
            parts.push_back(text.substr(start));
            return parts;
        }
        parts.push_back(text.substr(start, end - start));
        start = end + 1;
    }
}

std::string join(const StringList& parts, const std::string& separator) {
    std::string out;
    for (StringList::size_type i = 0; i < parts.size(); ++i) {
        if (i != 0) {
            out += separator;
        }
        out += parts[i];
    }
    return out;
}

bool startsWith(const std::string& text, const std::string& prefix) {
    return text.size() >= prefix.size() && text.compare(0, prefix.size(), prefix) == 0;
}

std::string replaceAll(const std::string& text, const std::string& from, const std::string& to) {
    if (from.empty()) {
        return text;
    }
    std::string out;
    std::string::size_type start = 0;
    std::string::size_type found;
    while ((found = text.find(from, start)) != std::string::npos) {
        out += text.substr(start, found - start);
        out += to;
        start = found + from.size();
    }
    return out + text.substr(start);
}

}  // namespace util
}  // namespace mm
