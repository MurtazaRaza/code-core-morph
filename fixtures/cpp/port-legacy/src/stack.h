#ifndef CALC_STACK_H
#define CALC_STACK_H

#include <cassert>
#include <cstddef>
#include <vector>

namespace calc {

// A LIFO stack whose pop() returns the element, which std::stack's doesn't.
template <typename T>
class Stack
{
public:
    bool empty() const { return items_.empty(); }
    std::size_t size() const { return items_.size(); }

    void push(const T& item) { items_.push_back(item); }

    T pop()
    {
        assert(!items_.empty());
        T item = items_.back();
        items_.pop_back();
        return item;
    }

    const T& top() const
    {
        assert(!items_.empty());
        return items_.back();
    }

private:
    std::vector<T> items_;
};

} // namespace calc

#endif
