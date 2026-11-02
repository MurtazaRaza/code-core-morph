#ifndef GEO_REGISTRY_H
#define GEO_REGISTRY_H

#include <map>
#include <string>
#include <vector>

#include "geo/types.h"

namespace geo {

// Keeps named entries and hands out stable ids.
class Registry {
public:
    typedef std::map<Id, std::string> NameTable;
    typedef NameTable::const_iterator const_iterator;
    typedef void (*Listener)(Id id, const std::string& name, void* context);

    Registry();

    Id add(const std::string& name);
    bool remove(Id id);
    bool contains(Id id) const;
    std::string nameOf(Id id) const;
    Id find(const std::string& name) const;
    Index size() const { return names_.size(); }

    void subscribe(Listener listener, void* context);

    const_iterator begin() const { return names_.begin(); }
    const_iterator end() const { return names_.end(); }

    static const Id kInvalid = 0;

private:
    typedef std::pair<Listener, void*> Subscription;
    typedef std::vector<Subscription> Subscriptions;
    typedef Subscriptions::const_iterator SubscriptionIterator;

    void notify(Id id, const std::string& name) const;

    NameTable names_;
    NameIndex index_;
    Subscriptions subscriptions_;
    Id next_;
};

}  // namespace geo

#endif  // GEO_REGISTRY_H
