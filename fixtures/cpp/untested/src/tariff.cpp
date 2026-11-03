#include "billing/tariff.h"

#include <sstream>

namespace billing {

Cents tieredPrice(unsigned int units) {
    if (units <= 100) {
        return units * 10;
    }
    Cents price = 100 * 10;
    if (units <= 500) {
        return price + (units - 100) * 8;
    }
    price += 400 * 8;
    return price + (units - 500) * 5;
}

unsigned int loyaltyDiscount(unsigned int years) {
    if (years >= 10) {
        return 15;
    }
    if (years >= 5) {
        return 10;
    }
    if (years >= 2) {
        return 5;
    }
    return 0;
}

Cents applyDiscount(Cents amount, unsigned int percent) {
    if (percent >= 100) {
        return 0;
    }
    return (amount * (100 - percent) + 50) / 100;
}

Cents total(const CentsList& charges, Cents cap) {
    Cents sum = 0;
    for (CentsList::const_iterator it = charges.begin(); it != charges.end(); ++it) {
        sum += *it;
        if (cap != 0 && sum > cap) {
            return cap;
        }
    }
    return sum;
}

std::string formatCents(Cents amount) {
    std::ostringstream out;
    out << amount / 100 << '.';
    unsigned int rest = amount % 100;
    if (rest < 10) {
        out << '0';
    }
    out << rest;
    return out.str();
}

}  // namespace billing
