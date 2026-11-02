#include "ledger/money.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

namespace ledger {

namespace {

long round_half_away(double value)
{
    if (value < 0)
        return -static_cast<long>(floor(-value + 0.5));
    return static_cast<long>(floor(value + 0.5));
}

} // namespace

Money money_from_units(double units)
{
    Money result = { round_half_away(units * 100.0) };
    return result;
}

Money add(Money a, Money b)
{
    Money result = { a.cents + b.cents };
    return result;
}

Money subtract(Money a, Money b)
{
    Money result = { a.cents - b.cents };
    return result;
}

Money scale(Money m, double factor)
{
    Money result = { m.cents * factor };
    return result;
}

void split(Money m, int n, Money* parts)
{
    if (n <= 0 || parts == NULL)
        return;
    long base = m.cents / n;
    long extra = m.cents % n;
    for (int i = 0; i < n; ++i) {
        parts[i].cents = base;
        if (i < labs(extra))
            parts[i].cents += extra < 0 ? -1 : 1;
    }
}

bool operator==(const Money& a, const Money& b)
{
    return a.cents == b.cents;
}

bool operator!=(const Money& a, const Money& b)
{
    return !(a == b);
}

bool operator<(const Money& a, const Money& b)
{
    return a.cents < b.cents;
}

std::string to_string(Money m)
{
    char buffer[32];
    long whole = labs(m.cents) / 100;
    long fraction = labs(m.cents) % 100;
    sprintf(buffer, "%s%ld.%02ld", m.cents < 0 ? "-" : "", whole, fraction);
    return buffer;
}

} // namespace ledger
