#include "pkgmeta/resolve.h"

#include <algorithm>
#include <exception>
#include <set>

namespace pkgmeta {

namespace {

// Records the end of a resolution in the trace: "aborted" when an exception is leaving.
class TraceScope {
public:
    explicit TraceScope(std::vector<std::string>* trace) : trace_(trace) {}
    ~TraceScope()
    {
        if (trace_ != nullptr && std::uncaught_exception())
            trace_->push_back("aborted");
    }

private:
    std::vector<std::string>* trace_;
};

enum class Mark { none, visiting, done };

struct Walk {
    const Registry& registry;
    std::vector<std::string>* trace;
    std::map<std::string, Mark> marks;
    std::vector<std::string> order;

    void note(const std::string& step)
    {
        if (trace != nullptr)
            trace->push_back(step);
    }

    void visit(const std::string& name)
    {
        Mark& mark = marks[name];
        if (mark == Mark::done)
            return;
        if (mark == Mark::visiting)
            throw ResolveError("dependency cycle through " + name);
        auto found = registry.find(name);
        if (found == registry.end())
            throw ResolveError("unknown package " + name);
        mark = Mark::visiting;
        note("visit " + name);
        std::vector<Dependency> dependencies = found->second.requires;
        std::sort(dependencies.begin(), dependencies.end(),
                  [](const Dependency& a, const Dependency& b) { return a.name < b.name; });
        for (const Dependency& dependency : dependencies) {
            auto target = registry.find(dependency.name);
            if (target != registry.end() &&
                !semver::satisfies(target->second.version, dependency.constraint)) {
                throw ResolveError(name + " needs " + dependency.name + " " +
                                   dependency.constraint + ", found " +
                                   target->second.version.to_string());
            }
            visit(dependency.name);
        }
        marks[name] = Mark::done;
        note("done " + name);
        order.push_back(name);
    }
};

} // namespace

std::vector<std::string> resolve(const Registry& registry, const std::string& root,
                                 std::vector<std::string>* trace)
{
    TraceScope scope(trace);
    Walk walk{registry, trace, {}, {}};
    walk.visit(root);
    return walk.order;
}

} // namespace pkgmeta
