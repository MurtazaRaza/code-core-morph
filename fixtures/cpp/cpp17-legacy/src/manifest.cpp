#include "pkgmeta/manifest.h"

#include <type_traits>

namespace pkgmeta {

namespace {

std::string_view trim(std::string_view text)
{
    while (!text.empty() && (text.front() == ' ' || text.front() == '\t' || text.front() == '\r'))
        text.remove_prefix(1);
    while (!text.empty() && (text.back() == ' ' || text.back() == '\t' || text.back() == '\r'))
        text.remove_suffix(1);
    return text;
}

std::string at_line(std::size_t line, const std::string& message)
{
    return "line " + std::to_string(line) + ": " + message;
}

static_assert(std::is_same<decltype(Manifest::requires), std::vector<Dependency>>::value,
              "requires holds dependencies");

} // namespace

ParseResult parse_manifest(std::string_view text, const ParseOptions& options)
{
    Manifest manifest;
    bool has_name = false;
    bool has_version = false;
    std::size_t number = 0;
    while (!text.empty()) {
        ++number;
        std::size_t newline = text.find('\n');
        std::string_view line = trim(text.substr(0, newline));
        text.remove_prefix(newline == std::string_view::npos ? text.size() : newline + 1);
        if (line.empty() || line.compare(0, 1, "#") == 0)
            continue;
        std::size_t colon = line.find(':');
        if (colon == std::string_view::npos)
            return {std::nullopt, at_line(number, "expected 'key: value'")};
        std::string_view key = trim(line.substr(0, colon));
        std::string_view value = trim(line.substr(colon + 1));
        if (key == "name") {
            manifest.name = std::string(value);
            has_name = !value.empty();
        } else if (key == "version") {
            auto version = semver::parse_version(value);
            if (!version)
                return {std::nullopt, at_line(number, "bad version '" + std::string(value) + "'")};
            manifest.version = *version;
            has_version = true;
        } else if (key == "requires") {
            std::size_t space = value.find(' ');
            Dependency dependency{std::string(value.substr(0, space)), "*"};
            if (space != std::string_view::npos)
                dependency.constraint = std::string(trim(value.substr(space + 1)));
            manifest.requires.push_back(dependency);
            if (static_cast<int>(manifest.requires.size()) > options.max_requires)
                return {std::nullopt, at_line(number, "too many dependencies")};
        } else if (key == "provides") {
            manifest.provides.emplace_back(value);
        } else if (options.strict) {
            return {std::nullopt, at_line(number, "unknown key '" + std::string(key) + "'")};
        }
    }
    if (!has_name)
        return {std::nullopt, "missing name"};
    if (!has_version)
        return {std::nullopt, "missing version"};
    return {manifest, ""};
}

} // namespace pkgmeta
