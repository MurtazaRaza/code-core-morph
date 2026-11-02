// C++ wrappers over the C core. C++ only: modernize freely here.
#ifndef CCORE_BUFFER_HPP
#define CCORE_BUFFER_HPP

#include <stdexcept>
#include <string>
#include <vector>

#include "ccore/buffer.h"
#include "ccore/hash.h"
#include "ccore/strlist.h"

namespace ccore {

typedef ccore_hash_t Hash;
typedef std::vector<std::string> Strings;

class Buffer {
public:
    Buffer();
    Buffer(const Buffer& other);
    Buffer& operator=(const Buffer& other);
    ~Buffer();

    Buffer& append(const std::string& text);
    Buffer& erase(size_t offset, size_t count);
    void clear();

    size_t size() const { return raw_.size; }
    std::string str() const;
    Hash hash() const;

private:
    ccore_buffer raw_;
};

// Owns a C list; exposes it as std::strings.
class StringList {
public:
    StringList();
    ~StringList();

    void push(const std::string& text);
    bool remove(const std::string& text);
    size_t length() const { return list_->length; }
    Strings items() const;
    std::string join(const std::string& separator) const;

private:
    StringList(const StringList&);
    StringList& operator=(const StringList&);

    ccore_list* list_;  // allocated with new, freed in the destructor
};

}  // namespace ccore

#endif  // CCORE_BUFFER_HPP
