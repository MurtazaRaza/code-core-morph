#ifndef LEDGER_MONEY_H
#define LEDGER_MONEY_H

#include <string>

namespace ledger {

// An amount in cents. An aggregate, so price tables can be brace-initialized.
struct Money
{
    long cents;
};

// 12.345 units -> 1235 cents; halves round away from zero.
Money money_from_units(double units);
Money add(Money a, Money b);
Money subtract(Money a, Money b);
// Multiplies and truncates toward zero, as the original billing code did.
Money scale(Money m, double factor);
// Splits m into n parts that differ by at most one cent; the first parts get the extra cents.
void split(Money m, int n, Money* parts);

bool operator==(const Money& a, const Money& b);
bool operator!=(const Money& a, const Money& b);
bool operator<(const Money& a, const Money& b);

// "12.35", "-0.05", "0.00".
std::string to_string(Money m);

} // namespace ledger

#endif
