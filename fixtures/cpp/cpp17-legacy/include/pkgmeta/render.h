#pragma once

#include <functional>
#include <map>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "pkgmeta/manifest.h"

namespace pkgmeta {

// Lets a temporary bind to an API that takes a non-const reference.
inline std::string& as_lvalue(std::string&& text)
{
    return text;
}

// Applies f to every value of a map, keeping the keys.
template <class Key, class Value, class F>
std::map<Key, std::result_of_t<F(const Value&)>> map_values(const std::map<Key, Value>& in, F f)
{
    std::map<Key, std::result_of_t<F(const Value&)>> out;
    for (const auto& [key, value] : in)
        out.emplace(key, f(value));
    return out;
}

// Renders a manifest's dependencies as an indented tree:
//   app 1.2.0
//   ├─ json >=3.0
//   └─ log *
std::string render_tree(const Manifest& manifest);

// Collects rendered lines; the prefix goes in front of every line.
class Report {
public:
    explicit Report(std::string prefix) : prefix_(std::move(prefix)) {}

    void add_all(const std::vector<std::string>& lines);
    // Appends the line to out if it isn't empty; returns whether it did.
    static bool append_line(std::string& out, const std::string& line);
    const std::vector<std::string>& lines() const { return lines_; }
    std::string text() const;

private:
    std::string prefix_;
    std::vector<std::string> lines_;
};

} // namespace pkgmeta
