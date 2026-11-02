#include "pkgmeta/version.h"

#include <charconv>

namespace pkgmeta {
namespace semver {

int Version::compare(const Version& other) const
{
    if (major_ != other.major_)
        return major_ < other.major_ ? -1 : 1;
    if (minor_ != other.minor_)
        return minor_ < other.minor_ ? -1 : 1;
    if (patch_ != other.patch_)
        return patch_ < other.patch_ ? -1 : 1;
    return 0;
}

std::string Version::to_string() const
{
    return std::to_string(major_) + "." + std::to_string(minor_) + "." + std::to_string(patch_);
}

std::optional<Version> parse_version(std::string_view text)
{
    int parts[3] = {0, 0, 0};
    std::size_t count = 0;
    const char* it = text.data();
    const char* end = text.data() + text.size();
    while (it != end) {
        if (count == 3)
            return std::nullopt;
        auto [next, error] = std::from_chars(it, end, parts[count]);
        if (error != std::errc() || parts[count] < 0)
            return std::nullopt;
        ++count;
        it = next;
        if (it == end)
            break;
        if (*it != '.' || it + 1 == end)
            return std::nullopt;
        ++it;
    }
    if (count == 0)
        return std::nullopt;
    return Version(parts[0], parts[1], parts[2]);
}

bool satisfies(const Version& version, std::string_view constraint)
{
    if (constraint == "*")
        return true;
    std::string_view op;
    for (std::string_view candidate : {">=", "<=", "^", ">", "<", "="}) {
        if (constraint.compare(0, candidate.size(), candidate) == 0) {
            op = candidate;
            break;
        }
    }
    auto bound = parse_version(constraint.substr(op.size()));
    if (!bound)
        return false;
    int order = version.compare(*bound);
    if (op == ">=")
        return order >= 0;
    if (op == "<=")
        return order <= 0;
    if (op == ">")
        return order > 0;
    if (op == "<")
        return order < 0;
    if (op == "^")
        return version.major() == bound->major() && order >= 0;
    return order == 0;
}

} // namespace semver
} // namespace pkgmeta
