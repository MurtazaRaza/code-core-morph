#include "money.hpp"

#include <cstdint>
#include <utility>
#include <vector>

#include "codecoremorph_exceptions.hpp"
#include "codecoremorph_java.hpp"

namespace shop {

namespace java = ccm::java;

namespace text {
namespace {
std::string trim(const std::string& s) {
    std::size_t begin = 0;
    std::size_t end = s.size();
    while (begin < end && static_cast<unsigned char>(s[begin]) <= 0x20) ++begin;
    while (end > begin && static_cast<unsigned char>(s[end - 1]) <= 0x20) --end;
    return s.substr(begin, end - begin);
}

std::vector<std::string> split_on_space(const std::string& s) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    for (std::size_t i = 0; i <= s.size(); ++i) {
        if (i == s.size() || s[i] == ' ') {
            parts.push_back(s.substr(start, i - start));
            start = i + 1;
        }
    }
    // Java keeps the input whole when nothing matched, and drops trailing empty strings.
    if (parts.size() == 1) return parts;
    while (!parts.empty() && parts.back().empty()) parts.pop_back();
    return parts;
}

int64_t parse_long(const std::string& s) {
    auto fail = [&]() -> ccm::NumberFormatException {
        return ccm::NumberFormatException("For input string: \"" + s + "\"");
    };
    if (s.empty()) throw fail();
    std::size_t i = 0;
    bool negative = false;
    if (s[0] == '-' || s[0] == '+') {
        negative = s[0] == '-';
        i = 1;
        if (s.size() == 1) throw fail();
    }
    // Accumulate negatively, as Long.parseLong does, so MIN_VALUE parses.
    int64_t result = 0;
    const int64_t limit = negative ? INT64_MIN : -INT64_MAX;
    const int64_t mult_min = limit / 10;
    for (; i < s.size(); ++i) {
        const char c = s[i];
        if (c < '0' || c > '9') throw fail();
        const int digit = c - '0';
        if (result < mult_min) throw fail();
        result *= 10;
        if (result < limit + digit) throw fail();
        result -= digit;
    }
    return negative ? result : -result;
}

}  // namespace
}  // namespace text

Money::Money(int64_t cents, std::string currency) : cents_(cents), currency_(std::move(currency)) {
    if (java::string_length(currency_) != 3) {
        throw ccm::IllegalArgumentException("a currency has three letters: " + currency_);
    }
}

Money Money::parse(const std::string& text) {
    const std::vector<std::string> parts = text::split_on_space(text::trim(text));
    if (parts.size() != 2) throw ccm::IllegalArgumentException("not an amount: " + text);
    std::string number = parts[0];
    const bool negative = !number.empty() && number[0] == '-';
    if (negative) number = number.substr(1);
    const std::size_t dot = number.find('.');
    const int64_t whole = text::parse_long(dot == std::string::npos ? number : number.substr(0, dot));
    int64_t frac = 0;
    if (dot != std::string::npos) {
        const std::string f = number.substr(dot + 1);
        if (java::string_length(f) != 2) throw ccm::NumberFormatException("two decimals: " + text);
        frac = text::parse_long(f);
    }
    const int64_t cents = java::add(java::mul<int64_t>(whole, 100), frac);
    return {negative ? java::neg(cents) : cents, parts[1]};
}

namespace {

void same_currency(const std::string& a, const std::string& b) {
    if (a != b) throw ccm::IllegalArgumentException("currencies differ: " + a + " and " + b);
}

}  // namespace

Money Money::plus(const Money& other) const {
    same_currency(currency_, other.currency_);
    return {java::add(cents_, other.cents_), currency_};
}

Money Money::times(int32_t n) const { return {java::mul<int64_t>(cents_, n), currency_}; }

Money Money::percent(int32_t percent) const {
    const int64_t scaled = java::mul<int64_t>(cents_, percent);
    const int64_t rounded = scaled >= 0 ? java::add<int64_t>(scaled, 50) / 100
                                        : java::neg(java::add<int64_t>(java::neg(scaled), 50) / 100);
    return {rounded, currency_};
}

int32_t Money::compare_to(const Money& other) const {
    same_currency(currency_, other.currency_);
    return cents_ < other.cents_ ? -1 : (cents_ == other.cents_ ? 0 : 1);
}

int32_t Money::hash_code() const noexcept {
    int32_t h = 17;
    h = java::add(java::mul<int32_t>(31, h), java::long_hash(cents_));
    h = java::add(java::mul<int32_t>(31, h), java::string_hash(currency_));
    return h;
}

std::string Money::to_string() const {
    // Math.abs(Long.MIN_VALUE) is Long.MIN_VALUE, as in Java.
    const int64_t abs = cents_ < 0 ? java::neg(cents_) : cents_;
    std::string frac = std::to_string(abs % 100);
    if (java::string_length(frac) < 2) frac = "0" + frac;
    return (cents_ < 0 ? "-" : "") + std::to_string(abs / 100) + "." + frac + " " + currency_;
}

}  // namespace shop
