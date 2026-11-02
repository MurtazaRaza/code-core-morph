#ifndef META_MATH_H
#define META_MATH_H

#include <cmath>
#include <cstdlib>
#include <type_traits>

#include "meta/traits.h"

namespace meta {

// Greatest common divisor, for integers only.
template <typename T>
typename std::enable_if<std::is_integral<T>::value, T>::type gcd(T a, T b) {
    while (b != 0) {
        T t = a % b;
        a = b;
        b = t;
    }
    return a < 0 ? -a : a;
}

template <typename T>
typename std::enable_if<std::is_integral<T>::value, T>::type lcm(T a, T b) {
    if (a == 0 || b == 0) {
        return 0;
    }
    T g = gcd(a, b);
    T result = a / g * b;
    return result < 0 ? -result : result;
}

// Linear interpolation, for floating point only.
template <typename T,
          typename = typename std::enable_if<std::is_floating_point<T>::value>::type>
T lerp(T a, T b, T t) {
    return a + (b - a) * t;
}

// clamp for any arithmetic type.
template <typename T>
typename std::enable_if<std::is_arithmetic<T>::value, T>::type clamp(T value, T low, T high) {
    return value < low ? low : (high < value ? high : value);
}

// Absolute difference: unsigned types can't go negative, so they get their own overload.
template <typename T>
typename std::enable_if<std::is_unsigned<T>::value, T>::type absDiff(T a, T b) {
    return a > b ? a - b : b - a;
}

template <typename T>
typename std::enable_if<std::is_signed<T>::value, T>::type absDiff(T a, T b) {
    T d = a - b;
    return d < 0 ? -d : d;
}

// Sum of any container of arithmetic values.
template <typename Container>
typename std::enable_if<is_container<Container>::value &&
                            std::is_arithmetic<typename Container::value_type>::value,
                        typename Container::value_type>::type
sum(const Container& values) {
    typedef typename Container::value_type T;
    typedef typename Container::const_iterator It;
    T total = T();
    for (It it = values.begin(); it != values.end(); ++it) {
        total += *it;
    }
    return total;
}

// Mean, always computed in double.
template <typename Container>
typename std::enable_if<is_container<Container>::value, double>::type mean(
    const Container& values) {
    if (values.size() == 0) {
        return 0.0;
    }
    return static_cast<double>(sum(values)) / static_cast<double>(values.size());
}

// Tag dispatch: power by repeated squaring for integers, std::pow for floating point.
namespace detail {
template <typename T>
T power(T base, unsigned exponent, std::true_type /*integral*/) {
    T result = 1;
    while (exponent > 0) {
        if (exponent & 1u) {
            result *= base;
        }
        base *= base;
        exponent >>= 1;
    }
    return result;
}

template <typename T>
T power(T base, unsigned exponent, std::false_type /*integral*/) {
    return static_cast<T>(std::pow(base, static_cast<T>(exponent)));
}
}  // namespace detail

template <typename T>
typename std::enable_if<std::is_arithmetic<T>::value, T>::type power(T base, unsigned exponent) {
    return detail::power(base, exponent, std::is_integral<T>());
}

}  // namespace meta

#endif  // META_MATH_H
