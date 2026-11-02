#pragma once

#include <optional>
#include <string>
#include <string_view>

namespace pkgmeta {
namespace semver {

class Version {
public:
    Version() = default;
    Version(int major, int minor, int patch) : major_(major), minor_(minor), patch_(patch) {}

    int major() const { return major_; }
    int minor() const { return minor_; }
    int patch() const { return patch_; }

    // Equal when all three numbers are equal.
    bool operator==(const Version& other) { return compare(other) == 0; }
    bool operator!=(const Version& other) { return compare(other) != 0; }
    bool operator<(const Version& other) const { return compare(other) < 0; }

    int compare(const Version& other) const;
    std::string to_string() const;

private:
    int major_ = 0;
    int minor_ = 0;
    int patch_ = 0;
};

// "1.2.3"; a missing minor or patch is 0 ("2" is 2.0.0). Nothing else may follow.
std::optional<Version> parse_version(std::string_view text);

// ">=1.2", "<2.0.0", "=1.4.1", "^1.2" (same major, at least 1.2), or "*".
bool satisfies(const Version& version, std::string_view constraint);

} // namespace semver
} // namespace pkgmeta
