#ifndef BILLING_TARIFF_H
#define BILLING_TARIFF_H

#include <string>
#include <vector>

namespace billing {

typedef unsigned int Cents;
typedef std::vector<Cents> CentsList;

// Price of `units` of usage under the tiered tariff: the first 100 units cost
// 10 cents each, the next 400 cost 8, every unit after that costs 5.
Cents tieredPrice(unsigned int units);

// Discount in percent for a customer with `years` of loyalty.
unsigned int loyaltyDiscount(unsigned int years);

// `amount` less `percent` percent, rounded half up.
Cents applyDiscount(Cents amount, unsigned int percent);

// The sum of `charges`, never more than `cap` (0 means no cap).
Cents total(const CentsList& charges, Cents cap);

// "12.34" for 1234 cents.
std::string formatCents(Cents amount);

}  // namespace billing

#endif
