#include "geo/registry.h"

namespace geo {

const Id Registry::kInvalid;

Registry::Registry() : next_(1) {}

Id Registry::add(const std::string& name) {
    NameIndex::const_iterator existing = index_.find(name);
    if (existing != index_.end()) {
        return existing->second;
    }
    Id id = next_++;
    names_[id] = name;
    index_[name] = id;
    notify(id, name);
    return id;
}

bool Registry::remove(Id id) {
    NameTable::iterator it = names_.find(id);
    if (it == names_.end()) {
        return false;
    }
    index_.erase(it->second);
    names_.erase(it);
    return true;
}

bool Registry::contains(Id id) const { return names_.find(id) != names_.end(); }

std::string Registry::nameOf(Id id) const {
    const_iterator it = names_.find(id);
    return it == names_.end() ? std::string() : it->second;
}

Id Registry::find(const std::string& name) const {
    NameIndex::const_iterator it = index_.find(name);
    return it == index_.end() ? kInvalid : it->second;
}

void Registry::subscribe(Listener listener, void* context) {
    subscriptions_.push_back(Subscription(listener, context));
}

void Registry::notify(Id id, const std::string& name) const {
    for (SubscriptionIterator it = subscriptions_.begin(); it != subscriptions_.end(); ++it) {
        it->first(id, name, it->second);
    }
}

}  // namespace geo
