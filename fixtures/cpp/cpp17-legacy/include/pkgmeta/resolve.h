#pragma once

#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "pkgmeta/manifest.h"

namespace pkgmeta {

class ResolveError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Available packages by name.
using Registry = std::map<std::string, Manifest>;

// Install order for root: every dependency before its dependents, ties in name order.
// Throws ResolveError for a missing package, an unsatisfied constraint or a cycle. Every step
// is appended to trace ("visit json", "done json", and "aborted" when resolution failed).
std::vector<std::string> resolve(const Registry& registry, const std::string& root,
                                 std::vector<std::string>* trace = nullptr);

} // namespace pkgmeta
