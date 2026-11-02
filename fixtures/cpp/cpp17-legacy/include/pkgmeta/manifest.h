#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "pkgmeta/version.h"

namespace pkgmeta {

struct Dependency {
    std::string name;
    std::string constraint;  // see semver::satisfies
};

struct Manifest {
    std::string name;
    semver::Version version;
    std::vector<Dependency> requires;
    std::vector<std::string> provides;
};

// Parser options. An aggregate: callers write ParseOptions{true, 4}.
struct ParseOptions {
    ParseOptions() = default;
    bool strict = false;    // unknown keys are errors instead of being ignored
    int max_requires = 64;  // more dependencies than this is an error
};

struct ParseResult {
    std::optional<Manifest> manifest;
    std::string error;  // "line 3: ..." when manifest is empty
};

// Lines of "key: value". Keys: name, version, requires (repeatable, "name constraint"),
// provides (repeatable). Blank lines and lines starting with '#' are ignored.
ParseResult parse_manifest(std::string_view text, const ParseOptions& options);

} // namespace pkgmeta
