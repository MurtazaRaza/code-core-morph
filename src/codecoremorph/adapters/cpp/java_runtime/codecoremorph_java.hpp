// codecoremorph_java.hpp: Java's semantics where C++'s differ, for C++ ports of Java code.
// Written by CodeCoreMorph; do not edit.
//
// Java's int and long arithmetic wraps (two's complement); C++'s signed overflow is undefined.
// Java's integer division by zero throws ArithmeticException, and MIN_VALUE / -1 is MIN_VALUE.
// Java masks a shift's count (x << 33 is x << 1 for an int). Java's strings are UTF-16: the
// port's std::string is UTF-8, so String.hashCode and lengths count UTF-16 units here.
#ifndef CODECOREMORPH_JAVA_HPP
#define CODECOREMORPH_JAVA_HPP

#include <cstdint>
#include <limits>
#include <string>
#include <type_traits>

#include "codecoremorph_exceptions.hpp"

namespace ccm::java {

template <class T>
using if_int = std::enable_if_t<std::is_same_v<T, int32_t> || std::is_same_v<T, int64_t> ||
                                    std::is_same_v<T, int16_t> || std::is_same_v<T, int8_t>,
                                T>;

// a + b, a - b, a * b and -a as Java computes them: wrapping.
template <class T>
constexpr if_int<T> add(T a, T b) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(static_cast<U>(a) + static_cast<U>(b));
}
template <class T>
constexpr if_int<T> sub(T a, T b) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(static_cast<U>(a) - static_cast<U>(b));
}
template <class T>
constexpr if_int<T> mul(T a, T b) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(static_cast<U>(a) * static_cast<U>(b));
}
template <class T>
constexpr if_int<T> neg(T a) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(U{0} - static_cast<U>(a));
}

// a / b and a % b as Java computes them: ArithmeticException("/ by zero"), MIN / -1 wraps.
template <class T>
if_int<T> div(T a, T b) {
    if (b == 0) throw ccm::ArithmeticException("/ by zero");
    if (b == -1) return neg(a);
    return a / b;
}
template <class T>
if_int<T> rem(T a, T b) {
    if (b == 0) throw ccm::ArithmeticException("/ by zero");
    if (b == -1) return 0;
    return a % b;
}

// a << n, a >> n and a >>> n: the count masked to the type's width, as Java does.
template <class T>
constexpr if_int<T> shl(T a, int n) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(static_cast<U>(a) << (n & (sizeof(T) * 8 - 1)));
}
template <class T>
constexpr if_int<T> shr(T a, int n) noexcept {
    return static_cast<T>(a >> (n & (sizeof(T) * 8 - 1)));
}
template <class T>
constexpr if_int<T> ushr(T a, int n) noexcept {
    using U = std::make_unsigned_t<T>;
    return static_cast<T>(static_cast<U>(a) >> (n & (sizeof(T) * 8 - 1)));
}

// Math.addExact and its kin: ArithmeticException on overflow.
template <class T>
if_int<T> add_exact(T a, T b) {
    T r;
    if (__builtin_add_overflow(a, b, &r)) {
        throw ccm::ArithmeticException(sizeof(T) == 8 ? "long overflow" : "integer overflow");
    }
    return r;
}
template <class T>
if_int<T> mul_exact(T a, T b) {
    T r;
    if (__builtin_mul_overflow(a, b, &r)) {
        throw ccm::ArithmeticException(sizeof(T) == 8 ? "long overflow" : "integer overflow");
    }
    return r;
}

// (int) of a long: the low 32 bits.
constexpr int32_t to_int(int64_t v) noexcept {
    return static_cast<int32_t>(static_cast<uint32_t>(static_cast<uint64_t>(v)));
}

// Long.hashCode(v): (int) (v ^ (v >>> 32)).
constexpr int32_t long_hash(int64_t v) noexcept { return to_int(v ^ ushr<int64_t>(v, 32)); }

// Boolean.hashCode.
constexpr int32_t bool_hash(bool v) noexcept { return v ? 1231 : 1237; }

// The UTF-16 code units of a UTF-8 string, one at a time (invalid bytes as U+FFFD).
template <class F>
void for_each_utf16(const std::string& utf8, F&& unit) {
    std::size_t i = 0;
    const std::size_t n = utf8.size();
    while (i < n) {
        auto b0 = static_cast<unsigned char>(utf8[i]);
        uint32_t cp = 0xFFFD;
        std::size_t len = 1;
        auto cont = [&](std::size_t k) {
            return i + k < n && (static_cast<unsigned char>(utf8[i + k]) & 0xC0) == 0x80;
        };
        auto bits = [&](std::size_t k) {
            return static_cast<uint32_t>(static_cast<unsigned char>(utf8[i + k]) & 0x3F);
        };
        if (b0 < 0x80) {
            cp = b0;
        } else if ((b0 & 0xE0) == 0xC0 && cont(1)) {
            cp = ((b0 & 0x1Fu) << 6) | bits(1);
            len = 2;
        } else if ((b0 & 0xF0) == 0xE0 && cont(1) && cont(2)) {
            cp = ((b0 & 0x0Fu) << 12) | (bits(1) << 6) | bits(2);
            len = 3;
        } else if ((b0 & 0xF8) == 0xF0 && cont(1) && cont(2) && cont(3)) {
            cp = ((b0 & 0x07u) << 18) | (bits(1) << 12) | (bits(2) << 6) | bits(3);
            len = 4;
        }
        if (cp >= 0x10000) {
            cp -= 0x10000;
            unit(static_cast<char16_t>(0xD800 + (cp >> 10)));
            unit(static_cast<char16_t>(0xDC00 + (cp & 0x3FF)));
        } else {
            unit(static_cast<char16_t>(cp));
        }
        i += len;
    }
}

// String.hashCode: s[0]*31^(n-1) + ... + s[n-1] over the UTF-16 units, wrapping.
inline int32_t string_hash(const std::string& utf8) {
    int32_t h = 0;
    for_each_utf16(utf8, [&](char16_t u) { h = add(mul<int32_t>(31, h), static_cast<int32_t>(u)); });
    return h;
}

// String.length: the UTF-16 units.
inline int32_t string_length(const std::string& utf8) {
    int32_t n = 0;
    for_each_utf16(utf8, [&](char16_t) { ++n; });
    return n;
}

}  // namespace ccm::java

#endif
