#ifndef SHOP_MONEY_HPP
#define SHOP_MONEY_HPP

#include <cstdint>
#include <string>

namespace shop {

// shop.Money: an amount of money in cents, with its currency. Immutable.
class Money {
public:
    // NullPointerException for no currency, IllegalArgumentException for one that hasn't three
    // letters (UTF-16 units, as Java counts).
    Money(int64_t cents, std::string currency);

    // Parses "12.34 EUR".
    static Money parse(const std::string& text);

    int64_t cents() const noexcept { return cents_; }
    const std::string& currency() const noexcept { return currency_; }

    Money plus(const Money& other) const;
    Money times(int32_t n) const;
    // This amount's percentage, rounded half up to the cent.
    Money percent(int32_t percent) const;
    bool is_zero() const noexcept { return cents_ == 0; }
    int32_t compare_to(const Money& other) const;
    bool operator==(const Money& other) const noexcept {
        return cents_ == other.cents_ && currency_ == other.currency_;
    }
    bool operator!=(const Money& other) const noexcept { return !(*this == other); }
    // Money.hashCode, as Java computes it.
    int32_t hash_code() const noexcept;
    std::string to_string() const;

private:
    int64_t cents_;
    std::string currency_;
};

}  // namespace shop

#endif
