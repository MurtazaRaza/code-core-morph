// codecoremorph_decimal.hpp: .NET's System.Decimal for C++ ports of C# code. Written by
// CodeCoreMorph; do not edit.
//
// A value is a 96-bit unsigned integer, a sign and a scale from 0 to 28, laid out as
// decimal.GetBits lays it out (lo, mid, hi, flags with the sign in bit 31 and the scale in bits
// 16 to 23), so values cross a bridge unchanged. Arithmetic keeps .NET's scales (1.10 + 1.0 is
// 2.10) and rounds half to even where a result needs more than 96 bits or a scale above 28, bit
// for bit as .NET does, zeros' signs and scales included; it throws the exceptions of
// codecoremorph_exceptions.hpp where .NET throws. Portable C++17.
#ifndef CODECOREMORPH_DECIMAL_HPP
#define CODECOREMORPH_DECIMAL_HPP

#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>

#include "codecoremorph_exceptions.hpp"

namespace ccm {

// What .NET throws: OverflowException and DivideByZeroException, with .NET's messages.
struct DecimalOverflow : OverflowException {
    DecimalOverflow() : OverflowException("Value was either too large or too small for a Decimal.") {}
};

struct DivideByZero : DivideByZeroException {
    DivideByZero() : DivideByZeroException("Attempted to divide by zero.") {}
};

namespace decimal_detail {

// A little-endian unsigned integer of 32-bit words, wide enough for every intermediate result.
struct Big {
    static constexpr int N = 10;
    std::array<std::uint32_t, N> w{};

    static Big of(std::uint32_t lo, std::uint32_t mid, std::uint32_t hi) {
        Big b;
        b.w[0] = lo;
        b.w[1] = mid;
        b.w[2] = hi;
        return b;
    }
    static Big small(std::uint64_t v) {
        Big b;
        b.w[0] = static_cast<std::uint32_t>(v);
        b.w[1] = static_cast<std::uint32_t>(v >> 32);
        return b;
    }
    bool zero() const {
        for (std::uint32_t x : w) {
            if (x != 0) return false;
        }
        return true;
    }
    bool fits96() const {
        for (int i = 3; i < N; ++i) {
            if (w[i] != 0) return false;
        }
        return true;
    }
    bool odd() const { return (w[0] & 1u) != 0; }
};

inline int cmp(const Big& a, const Big& b) {
    for (int i = Big::N - 1; i >= 0; --i) {
        if (a.w[i] != b.w[i]) return a.w[i] < b.w[i] ? -1 : 1;
    }
    return 0;
}

inline Big add(const Big& a, const Big& b) {
    Big r;
    std::uint64_t carry = 0;
    for (int i = 0; i < Big::N; ++i) {
        std::uint64_t s = std::uint64_t{a.w[i]} + b.w[i] + carry;
        r.w[i] = static_cast<std::uint32_t>(s);
        carry = s >> 32;
    }
    return r;
}

// a - b, for a >= b.
inline Big sub(const Big& a, const Big& b) {
    Big r;
    std::int64_t borrow = 0;
    for (int i = 0; i < Big::N; ++i) {
        std::int64_t d = std::int64_t{a.w[i]} - b.w[i] - borrow;
        borrow = d < 0 ? 1 : 0;
        r.w[i] = static_cast<std::uint32_t>(d + (borrow << 32));
    }
    return r;
}

inline Big mul_small(const Big& a, std::uint32_t m) {
    Big r;
    std::uint64_t carry = 0;
    for (int i = 0; i < Big::N; ++i) {
        std::uint64_t p = std::uint64_t{a.w[i]} * m + carry;
        r.w[i] = static_cast<std::uint32_t>(p);
        carry = p >> 32;
    }
    return r;
}

inline Big mul(const Big& a, const Big& b) {
    Big r;
    for (int i = 0; i < Big::N; ++i) {
        if (a.w[i] == 0) continue;
        std::uint64_t carry = 0;
        for (int j = 0; i + j < Big::N; ++j) {
            std::uint64_t cur = std::uint64_t{r.w[i + j]} + std::uint64_t{a.w[i]} * b.w[j] + carry;
            r.w[i + j] = static_cast<std::uint32_t>(cur);
            carry = cur >> 32;
        }
    }
    return r;
}

inline Big pow10(int k) {
    Big r = Big::small(1);
    while (k >= 9) {
        r = mul_small(r, 1000000000u);
        k -= 9;
    }
    static const std::uint32_t small[] = {1, 10, 100, 1000, 10000, 100000, 1000000, 10000000,
                                          100000000};
    return mul_small(r, small[k]);
}

inline Big shl1(const Big& a) {
    Big r;
    std::uint32_t carry = 0;
    for (int i = 0; i < Big::N; ++i) {
        r.w[i] = (a.w[i] << 1) | carry;
        carry = a.w[i] >> 31;
    }
    return r;
}

// Long division: a = q * d + r, for d != 0.
inline void divmod(const Big& a, const Big& d, Big& q, Big& r) {
    q = Big{};
    r = Big{};
    for (int bit = Big::N * 32 - 1; bit >= 0; --bit) {
        r = shl1(r);
        r.w[0] |= (a.w[bit / 32] >> (bit % 32)) & 1u;
        if (cmp(r, d) >= 0) {
            r = sub(r, d);
            q.w[bit / 32] |= 1u << (bit % 32);
        }
    }
}

// q rounded half to even, given the remainder r of a division by d.
inline Big round_half_even(const Big& q, const Big& r, const Big& d) {
    int c = cmp(shl1(r), d);
    if (c > 0 || (c == 0 && q.odd())) return add(q, Big::small(1));
    return q;
}

}  // namespace decimal_detail

class Decimal {
public:
    static constexpr int MaxScale = 28;

    constexpr Decimal() = default;
    Decimal(std::int64_t v) {  // NOLINT(google-explicit-constructor): C# converts implicitly
        std::uint64_t m = v < 0 ? 0 - static_cast<std::uint64_t>(v) : static_cast<std::uint64_t>(v);
        lo_ = static_cast<std::uint32_t>(m);
        mid_ = static_cast<std::uint32_t>(m >> 32);
        flags_ = v < 0 ? SignBit : 0;
    }
    Decimal(int v) : Decimal(static_cast<std::int64_t>(v)) {}  // NOLINT(google-explicit-constructor)

    // The value of decimal.GetBits: lo, mid, hi and flags.
    static Decimal from_bits(std::int32_t lo, std::int32_t mid, std::int32_t hi, std::int32_t flags) {
        Decimal d;
        d.lo_ = static_cast<std::uint32_t>(lo);
        d.mid_ = static_cast<std::uint32_t>(mid);
        d.hi_ = static_cast<std::uint32_t>(hi);
        d.flags_ = static_cast<std::uint32_t>(flags) & (SignBit | ScaleMask);
        if (d.scale() > MaxScale) throw std::invalid_argument("a decimal's scale is at most 28");
        return d;
    }
    std::array<std::int32_t, 4> bits() const {
        return {static_cast<std::int32_t>(lo_), static_cast<std::int32_t>(mid_),
                static_cast<std::int32_t>(hi_), static_cast<std::int32_t>(flags_)};
    }

    // A value written as C# writes a literal (-12.50), at the scale written.
    static Decimal parse(const std::string& text);

    int scale() const { return static_cast<int>((flags_ & ScaleMask) >> 16); }
    bool negative() const { return (flags_ & SignBit) != 0; }
    bool is_zero() const { return lo_ == 0 && mid_ == 0 && hi_ == 0; }

    Decimal operator-() const {
        Decimal d = *this;
        d.flags_ ^= SignBit;
        return d;
    }
    friend Decimal operator+(const Decimal& a, const Decimal& b) { return add(a, b, false); }
    friend Decimal operator-(const Decimal& a, const Decimal& b) { return add(a, b, true); }
    friend Decimal operator*(const Decimal& a, const Decimal& b) {
        using namespace decimal_detail;
        int scale = a.scale() + b.scale();
        bool neg = a.negative() != b.negative();
        // .NET's paths: two 32-bit operands keep a zero product's scale and sign, up to a scale
        // past which the product is plain zero; any other zero product is plain zero.
        bool small = a.mid_ == 0 && a.hi_ == 0 && b.mid_ == 0 && b.hi_ == 0;
        if (small && scale > MaxScale + 19) return Decimal();
        if (!small && (a.is_zero() || b.is_zero())) return Decimal();
        return fit(mul(a.mant(), b.mant()), scale, neg);
    }
    friend Decimal operator/(const Decimal& a, const Decimal& b) { return divide(a, b); }
    friend Decimal operator%(const Decimal& a, const Decimal& b) { return a - truncate(a / b) * b; }
    Decimal& operator+=(const Decimal& o) { return *this = *this + o; }
    Decimal& operator-=(const Decimal& o) { return *this = *this - o; }
    Decimal& operator*=(const Decimal& o) { return *this = *this * o; }
    Decimal& operator/=(const Decimal& o) { return *this = *this / o; }

    friend int compare(const Decimal& a, const Decimal& b) {
        using namespace decimal_detail;
        if (a.is_zero() && b.is_zero()) return 0;
        if (a.negative() != b.negative()) return a.negative() ? -1 : 1;
        int s = a.scale() > b.scale() ? a.scale() : b.scale();
        int c = cmp(mul(a.mant(), pow10(s - a.scale())), mul(b.mant(), pow10(s - b.scale())));
        return a.negative() ? -c : c;
    }
    friend bool operator==(const Decimal& a, const Decimal& b) { return compare(a, b) == 0; }
    friend bool operator!=(const Decimal& a, const Decimal& b) { return compare(a, b) != 0; }
    friend bool operator<(const Decimal& a, const Decimal& b) { return compare(a, b) < 0; }
    friend bool operator<=(const Decimal& a, const Decimal& b) { return compare(a, b) <= 0; }
    friend bool operator>(const Decimal& a, const Decimal& b) { return compare(a, b) > 0; }
    friend bool operator>=(const Decimal& a, const Decimal& b) { return compare(a, b) >= 0; }

    // Math.Round(d, decimals): half to even, or half away from zero.
    friend Decimal round(const Decimal& d, int decimals = 0, bool away_from_zero = false) {
        using namespace decimal_detail;
        if (decimals < 0 || decimals > MaxScale) throw std::out_of_range("decimals");
        if (d.scale() <= decimals) return d;
        Big div = pow10(d.scale() - decimals), q, r;
        divmod(d.mant(), div, q, r);
        int c = cmp(shl1(r), div);
        if (c > 0 || (c == 0 && (away_from_zero || q.odd()))) {
            q = decimal_detail::add(q, Big::small(1));
        }
        return make(q, decimals, d.negative());
    }
    friend Decimal truncate(const Decimal& d) {
        using namespace decimal_detail;
        Big q, r;
        divmod(d.mant(), pow10(d.scale()), q, r);
        return make(q, 0, d.negative());
    }

    // decimal.ToString(CultureInfo.InvariantCulture).
    std::string to_string() const {
        using namespace decimal_detail;
        std::string digits;
        Big m = mant();
        Big ten = Big::small(10), q, r;
        while (!m.zero()) {
            divmod(m, ten, q, r);
            digits.insert(digits.begin(), static_cast<char>('0' + r.w[0]));
            m = q;
        }
        int s = scale();
        while (static_cast<int>(digits.size()) <= s) digits.insert(digits.begin(), '0');
        if (s > 0) digits.insert(digits.end() - s, '.');
        if (negative() && !is_zero()) digits.insert(digits.begin(), '-');
        return digits;
    }

    double to_double() const {
        double m = static_cast<double>(hi_) * 18446744073709551616.0 +
                   static_cast<double>(mid_) * 4294967296.0 + static_cast<double>(lo_);
        for (int i = 0; i < scale(); ++i) m /= 10.0;
        return negative() ? -m : m;
    }

    // Equal values hash equally, whatever their scales (1.10 and 1.1).
    std::size_t hash() const {
        using namespace decimal_detail;
        Big m = mant(), ten = Big::small(10), q, r;
        int s = scale();
        while (s > 0 && !m.zero()) {
            divmod(m, ten, q, r);
            if (!r.zero()) break;
            m = q;
            --s;
        }
        if (m.zero()) return 0;
        std::size_t h = std::hash<std::uint32_t>{}(m.w[0]);
        for (std::uint32_t x : {m.w[1], m.w[2], static_cast<std::uint32_t>(s), flags_ & SignBit}) {
            h = h * 1000003u ^ std::hash<std::uint32_t>{}(x);
        }
        return h;
    }

private:
    static constexpr std::uint32_t SignBit = 0x80000000u;
    static constexpr std::uint32_t ScaleMask = 0x00FF0000u;

    std::uint32_t lo_ = 0, mid_ = 0, hi_ = 0, flags_ = 0;

    decimal_detail::Big mant() const { return decimal_detail::Big::of(lo_, mid_, hi_); }

    static Decimal make(const decimal_detail::Big& m, int scale, bool negative) {
        Decimal d;
        d.lo_ = m.w[0];
        d.mid_ = m.w[1];
        d.hi_ = m.w[2];
        d.flags_ = (static_cast<std::uint32_t>(scale) << 16) | (negative ? SignBit : 0);
        return d;
    }

    // An exact mantissa and scale made to fit: the fewest digits dropped, rounded half to even.
    static Decimal fit(const decimal_detail::Big& m, int scale, bool negative) {
        using namespace decimal_detail;
        int k = scale > MaxScale ? scale - MaxScale : 0;
        if (k == 0 && m.fits96()) return make(m, scale, negative);
        for (; k <= scale; ++k) {
            Big div = pow10(k), q, r;
            divmod(m, div, q, r);
            q = round_half_even(q, r, div);
            if (q.fits96()) return make(q, scale - k, negative);
        }
        throw DecimalOverflow();
    }

    static Decimal add(const Decimal& a, const Decimal& b, bool subtract) {
        using namespace decimal_detail;
        int s = a.scale() > b.scale() ? a.scale() : b.scale();
        Big ma = mul(a.mant(), pow10(s - a.scale()));
        Big mb = mul(b.mant(), pow10(s - b.scale()));
        bool nb = b.negative() != subtract;
        if (a.negative() == nb) return fit(decimal_detail::add(ma, mb), s, a.negative());
        int c = cmp(ma, mb);
        // .NET takes the operand of the larger scale first, and a zero keeps its sign.
        if (c == 0) return make(Big{}, s, b.scale() > a.scale() ? nb : a.negative());
        return c > 0 ? fit(sub(ma, mb), s, a.negative()) : fit(sub(mb, ma), s, nb);
    }

    // The exact quotient at the smallest scale from scale(a) - scale(b) up, else rounded at the
    // largest scale whose quotient fits, its trailing zeros then dropped (.NET's unscaling). A
    // quotient that rounds to zero is 0 at scale 0, with its sign.
    static Decimal divide(const Decimal& a, const Decimal& b) {
        using namespace decimal_detail;
        if (b.is_zero()) throw DivideByZero();
        bool neg = a.negative() != b.negative();
        int s0 = a.scale() - b.scale();
        int natural = s0 > 0 ? s0 : 0;
        int s = natural;
        Big mb = b.mant(), q, r;
        for (; s <= MaxScale; ++s) {
            divmod(mul(a.mant(), pow10(s - s0)), mb, q, r);
            if (!q.fits96()) break;
            if (r.zero()) return make(q, s, neg);
        }
        for (--s; s >= natural; --s) {
            divmod(mul(a.mant(), pow10(s - s0)), mb, q, r);
            Big rounded = round_half_even(q, r, mb);
            if (!rounded.fits96()) continue;
            if (rounded.zero()) return make(rounded, 0, neg);
            Big ten = Big::small(10), q10, r10;
            while (s > 0) {
                divmod(rounded, ten, q10, r10);
                if (!r10.zero()) break;
                rounded = q10;
                --s;
            }
            return make(rounded, s, neg);
        }
        throw DecimalOverflow();
    }
};

inline Decimal Decimal::parse(const std::string& text) {
    using namespace decimal_detail;
    std::size_t i = 0;
    bool neg = false;
    if (i < text.size() && (text[i] == '-' || text[i] == '+')) neg = text[i++] == '-';
    Big m;
    int scale = -1;
    bool any = false;
    for (; i < text.size(); ++i) {
        char c = text[i];
        if (c == '.' && scale < 0) {
            scale = 0;
            continue;
        }
        if (c < '0' || c > '9') throw FormatException("The input string '" + text + "' was not in a correct format.");
        m = decimal_detail::add(mul_small(m, 10), Big::small(static_cast<std::uint32_t>(c - '0')));
        if (scale >= 0) ++scale;
        any = true;
    }
    if (!any) throw FormatException("The input string '" + text + "' was not in a correct format.");
    return fit(m, scale < 0 ? 0 : scale, neg);
}

inline std::string to_string(const Decimal& d) { return d.to_string(); }

}  // namespace ccm

template <>
struct std::hash<ccm::Decimal> {
    std::size_t operator()(const ccm::Decimal& d) const noexcept { return d.hash(); }
};

#endif
