#ifndef SHOP_MONEY_HPP
#define SHOP_MONEY_HPP

#include <cstddef>
#include <functional>
#include <string>

#include "codecoremorph_decimal.hpp"

namespace shop {

// An amount of money in one currency.
class Money {
public:
    // C#'s default(Money): zero, no currency.
    Money() = default;
    Money(ccm::Decimal amount, std::string currency);

    const ccm::Decimal& amount() const { return amount_; }
    const std::string& currency() const { return currency_; }

    static Money zero(std::string currency);

    Money add(const Money& other) const;
    Money subtract(const Money& other) const;
    Money times(int quantity) const;
    // The given percentage of this amount, unrounded.
    Money percent(int percent) const;

    friend Money operator+(const Money& a, const Money& b) { return a.add(b); }
    friend Money operator-(const Money& a, const Money& b) { return a.subtract(b); }
    friend bool operator==(const Money& a, const Money& b) { return a.equals(b); }
    friend bool operator!=(const Money& a, const Money& b) { return !a.equals(b); }

    bool equals(const Money& other) const;
    std::size_t hash() const;
    std::string to_string() const;

private:
    void check_currency(const Money& other) const;

    ccm::Decimal amount_;
    std::string currency_;
};

}  // namespace shop

template <>
struct std::hash<shop::Money> {
    std::size_t operator()(const shop::Money& m) const noexcept { return m.hash(); }
};

#endif
