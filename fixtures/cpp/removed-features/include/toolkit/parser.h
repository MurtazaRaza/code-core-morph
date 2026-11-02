#ifndef TOOLKIT_PARSER_H
#define TOOLKIT_PARSER_H

#include <map>
#include <stdexcept>
#include <string>

namespace toolkit {

class ParseError : public std::runtime_error {
public:
    ParseError(const std::string& message, int line)
        : std::runtime_error(message), line_(line) {}
    int line() const throw() { return line_; }

private:
    int line_;
};

// Parses "key = value" lines; '#' starts a comment.
class ConfigParser {
public:
    ConfigParser() {}

    void parse(const std::string& text) throw(ParseError);
    bool has(const std::string& key) const throw();
    std::string get(const std::string& key) const throw(std::out_of_range);
    int getInt(const std::string& key) const throw(std::out_of_range, std::invalid_argument);
    int size() const throw() { return static_cast<int>(values_.size()); }

private:
    std::map<std::string, std::string> values_;
};

}  // namespace toolkit

#endif  // TOOLKIT_PARSER_H
