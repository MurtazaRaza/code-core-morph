#include "ccore/buffer.hpp"

#include <cstdlib>
#include <new>

namespace ccore {

namespace {
typedef std::vector<std::string>* Collector;

void collect(const char* text, void* context) {
    static_cast<Collector>(context)->push_back(text);
}

void check(ccore_status status) {
    if (status == CCORE_NO_MEMORY) {
        throw std::bad_alloc();
    }
    if (status == CCORE_OUT_OF_RANGE) {
        throw std::out_of_range("buffer range");
    }
}
}  // namespace

Buffer::Buffer() { ccore_buffer_init(&raw_); }

Buffer::Buffer(const Buffer& other) {
    ccore_buffer_init(&raw_);
    check(ccore_buffer_append(&raw_, other.raw_.data, other.raw_.size));
}

Buffer& Buffer::operator=(const Buffer& other) {
    if (this != &other) {
        ccore_buffer_clear(&raw_);
        check(ccore_buffer_append(&raw_, other.raw_.data, other.raw_.size));
    }
    return *this;
}

Buffer::~Buffer() { ccore_buffer_free(&raw_); }

Buffer& Buffer::append(const std::string& text) {
    check(ccore_buffer_append(&raw_, text.data(), text.size()));
    return *this;
}

Buffer& Buffer::erase(size_t offset, size_t count) {
    check(ccore_buffer_erase(&raw_, offset, count));
    return *this;
}

void Buffer::clear() { ccore_buffer_clear(&raw_); }

std::string Buffer::str() const {
    return raw_.size ? std::string(reinterpret_cast<const char*>(raw_.data), raw_.size)
                     : std::string();
}

Hash Buffer::hash() const { return ccore_fnv1a(raw_.data, raw_.size); }

StringList::StringList() : list_(new ccore_list) { ccore_list_init(list_); }

StringList::~StringList() {
    ccore_list_free(list_);
    delete list_;
}

void StringList::push(const std::string& text) {
    if (!ccore_list_push(list_, text.c_str())) {
        throw std::bad_alloc();
    }
}

bool StringList::remove(const std::string& text) {
    return ccore_list_remove(list_, text.c_str()) != 0;
}

Strings StringList::items() const {
    Strings out;
    ccore_list_visit(list_, collect, &out);
    return out;
}

std::string StringList::join(const std::string& separator) const {
    char* joined = ccore_list_join(list_, separator.c_str());
    if (joined == NULL) {
        throw std::bad_alloc();
    }
    std::string out(joined);
    std::free(joined);
    return out;
}

}  // namespace ccore
