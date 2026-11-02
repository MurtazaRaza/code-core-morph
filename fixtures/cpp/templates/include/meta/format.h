#ifndef META_FORMAT_H
#define META_FORMAT_H

#include <sstream>
#include <string>
#include <type_traits>
#include <utility>

#include "meta/traits.h"

namespace meta {

// toString picks an overload with enable_if: strings, booleans, numbers, pairs, containers.
std::string quote(const std::string& text);

template <typename T>
typename std::enable_if<is_string<T>::value, std::string>::type toString(const T& value) {
    return quote(value);
}

inline std::string toString(bool value) { return value ? "true" : "false"; }

template <typename T>
typename std::enable_if<std::is_arithmetic<T>::value && !std::is_same<T, bool>::value,
                        std::string>::type
toString(T value) {
    std::ostringstream out;
    out << value;
    return out.str();
}

template <typename T>
typename std::enable_if<is_pair<T>::value, std::string>::type toString(const T& value);

template <typename T>
typename std::enable_if<is_container<T>::value, std::string>::type toString(const T& value);

template <typename T>
typename std::enable_if<is_pair<T>::value, std::string>::type toString(const T& value) {
    return "(" + toString(value.first) + ", " + toString(value.second) + ")";
}

template <typename T>
typename std::enable_if<is_container<T>::value, std::string>::type toString(const T& value) {
    typedef typename T::const_iterator Iterator;
    std::string out = "[";
    bool first = true;
    for (Iterator it = value.begin(); it != value.end(); ++it) {
        if (!first) {
            out += ", ";
        }
        out += toString(*it);
        first = false;
    }
    return out + "]";
}

// A value with a unit, printable only when the value type is arithmetic.
template <typename T, typename Enable = void>
class Quantity;

template <typename T>
class Quantity<T, typename std::enable_if<std::is_arithmetic<T>::value>::type> {
public:
    typedef T value_type;

    Quantity(T value, const std::string& unit) : value_(value), unit_(unit) {}

    T value() const { return value_; }
    const std::string& unit() const { return unit_; }
    std::string str() const { return toString(value_) + " " + unit_; }

    template <typename U>
    typename std::enable_if<std::is_convertible<U, T>::value, Quantity>::type scaled(
        U factor) const {
        return Quantity(static_cast<T>(value_ * static_cast<T>(factor)), unit_);
    }

private:
    T value_;
    std::string unit_;
};

}  // namespace meta

#endif  // META_FORMAT_H
