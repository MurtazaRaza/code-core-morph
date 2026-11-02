#ifndef CCORE_TABLE_HPP
#define CCORE_TABLE_HPP

#include <map>
#include <string>

#include "ccore/hash.h"

namespace ccore {

// A string interning table: the C set for membership, a C++ map for ids.
class InternTable {
public:
    typedef std::map<std::string, int> Ids;

    explicit InternTable(size_t capacity);
    ~InternTable();

    int intern(const std::string& text);  // the id, new or existing
    bool contains(const std::string& text) const;
    int count() const { return static_cast<int>(ids_.size()); }

private:
    InternTable(const InternTable&);
    InternTable& operator=(const InternTable&);

    ccore_set set_;
    Ids ids_;
};

}  // namespace ccore

#endif  // CCORE_TABLE_HPP
