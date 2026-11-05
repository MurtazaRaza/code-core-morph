#include "calc/strutil.h"

namespace calc {
namespace strutil {

namespace {

bool is_space(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

} // namespace

std::string trim(const std::string& s)
{
    std::string::size_type begin = 0;
    std::string::size_type end = s.size();
    while (begin < end && is_space(s[begin]))
        ++begin;
    while (end > begin && is_space(s[end - 1]))
        --end;
    return s.substr(begin, end - begin);
}

std::vector<std::string> split(const std::string& s, char sep)
{
    std::vector<std::string> fields;
    std::string::size_type start = 0;
    for (;;) {
        std::string::size_type at = s.find(sep, start);
        if (at == std::string::npos) {
            fields.push_back(s.substr(start));
            return fields;
        }
        fields.push_back(s.substr(start, at - start));
        start = at + 1;
    }
}

std::string join(const std::vector<std::string>& parts, const std::string& sep)
{
    std::string out;
    for (std::vector<std::string>::size_type i = 0; i < parts.size(); ++i) {
        if (i != 0)
            out += sep;
        out += parts[i];
    }
    return out;
}

std::string to_lower(const std::string& s)
{
    std::string out(s);
    for (std::string::size_type i = 0; i < out.size(); ++i) {
        if (out[i] >= 'A' && out[i] <= 'Z')
            out[i] = static_cast<char>(out[i] - 'A' + 'a');
    }
    return out;
}

bool starts_with(const std::string& s, const std::string& prefix)
{
    return s.size() >= prefix.size() && s.compare(0, prefix.size(), prefix) == 0;
}

int replace_all(std::string& s, const std::string& from, const std::string& to)
{
    int count = 0;
    std::string::size_type at = 0;
    while ((at = s.find(from, at)) != std::string::npos) {
        s.replace(at, from.size(), to);
        at += to.size();
        ++count;
    }
    return count;
}

} // namespace strutil
} // namespace calc
