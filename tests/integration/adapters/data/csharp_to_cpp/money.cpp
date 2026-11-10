#include "money.hpp"

#include <utility>

#include "codecoremorph_exceptions.hpp"

namespace shop {

Money::Money(ccm::Decimal amount, std::string currency)
    : amount_(amount), currency_(std::move(currency)) {
    if (currency_.size() != 3) {
        throw ccm::ArgumentException("a currency is a three-letter code", "currency");
    }
}

Money Money::zero(std::string currency) { return Money(ccm::Decimal(0), std::move(currency)); }

Money Money::add(const Money& other) const {
    check_currency(other);
    return Money(amount_ + other.amount_, currency_);
}

Money Money::subtract(const Money& other) const {
    check_currency(other);
    return Money(amount_ - other.amount_, currency_);
}

Money Money::times(int quantity) const { return Money(amount_ * ccm::Decimal(quantity), currency_); }

Money Money::percent(int percent) const {
    return Money(amount_ * ccm::Decimal(percent) / ccm::Decimal(100), currency_);
}

bool Money::equals(const Money& other) const {
    return amount_ == other.amount_ && currency_ == other.currency_;
}

std::size_t Money::hash() const {
    return amount_.hash() * 31 + std::hash<std::string>{}(currency_);
}

std::string Money::to_string() const { return amount_.to_string() + " " + currency_; }

void Money::check_currency(const Money& other) const {
    if (other.currency_ != currency_) {
        throw ccm::InvalidOperationException("cannot combine " + other.currency_ + " with " +
                                             currency_);
    }
}

}  // namespace shop
