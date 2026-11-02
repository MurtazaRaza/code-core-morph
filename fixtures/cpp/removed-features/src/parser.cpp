#include "toolkit/parser.h"

#include <cstdlib>

namespace toolkit {

namespace {

std::string trim(const std::string& text) {
    std::string::size_type first = text.find_first_not_of(" \t\r");
    if (first == std::string::npos) {
        return std::string();
    }
    std::string::size_type last = text.find_last_not_of(" \t\r");
    return text.substr(first, last - first + 1);
}

}  // namespace

void ConfigParser::parse(const std::string& text) throw(ParseError) {
    int lineNumber = 0;
    std::string::size_type start = 0;
    while (start <= text.size()) {
        std::string::size_type end = text.find('\n', start);
        if (end == std::string::npos) {
            end = text.size();
        }
        ++lineNumber;
        std::string line = trim(text.substr(start, end - start));
        start = end + 1;
        if (line.empty() || line[0] == '#') {
            continue;
        }
        std::string::size_type equals = line.find('=');
        if (equals == std::string::npos) {
            throw ParseError("expected key = value", lineNumber);
        }
        std::string key = trim(line.substr(0, equals));
        if (key.empty()) {
            throw ParseError("empty key", lineNumber);
        }
        values_[key] = trim(line.substr(equals + 1));
    }
}

bool ConfigParser::has(const std::string& key) const throw() {
    return values_.find(key) != values_.end();
}

std::string ConfigParser::get(const std::string& key) const throw(std::out_of_range) {
    std::map<std::string, std::string>::const_iterator it = values_.find(key);
    if (it == values_.end()) {
        throw std::out_of_range("no key " + key);
    }
    return it->second;
}

int ConfigParser::getInt(const std::string& key) const
    throw(std::out_of_range, std::invalid_argument) {
    std::string text = get(key);
    char* end = 0;
    long value = std::strtol(text.c_str(), &end, 10);
    if (text.empty() || *end != '\0') {
        throw std::invalid_argument(key + " is not a number");
    }
    return static_cast<int>(value);
}

}  // namespace toolkit
