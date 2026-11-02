#include "ccore/table.hpp"

#include <new>

namespace ccore {

InternTable::InternTable(size_t capacity) {
    if (!ccore_set_init(&set_, capacity)) {
        throw std::bad_alloc();
    }
}

InternTable::~InternTable() { ccore_set_free(&set_); }

int InternTable::intern(const std::string& text) {
    Ids::const_iterator it = ids_.find(text);
    if (it != ids_.end()) {
        return it->second;
    }
    if (ccore_set_add(&set_, text.c_str()) < 0) {
        throw std::bad_alloc();
    }
    int id = static_cast<int>(ids_.size()) + 1;
    ids_[text] = id;
    return id;
}

bool InternTable::contains(const std::string& text) const {
    return ccore_set_contains(&set_, text.c_str()) != 0;
}

}  // namespace ccore
