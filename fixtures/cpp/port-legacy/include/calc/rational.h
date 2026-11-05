#ifndef CALC_RATIONAL_H
#define CALC_RATIONAL_H

#include <iosfwd>
#include <string>

namespace calc {

// A fraction in lowest terms, with a positive denominator.
class Rational
{
public:
    // Throws std::domain_error when den is zero.
    Rational(long num = 0, long den = 1);

    long num() const { return num_; }
    long den() const { return den_; }
    bool is_integer() const { return den_ == 1; }

    // Rounds toward zero.
    long truncate() const;
    double to_double() const;
    // "7/2", "-1/3", "4".
    std::string str() const;

    Rational& operator+=(const Rational& other);
    Rational& operator-=(const Rational& other);
    Rational& operator*=(const Rational& other);
    // Throws std::domain_error when other is zero, and leaves this unchanged.
    Rational& operator/=(const Rational& other);

    // Reads "3", "-7/2" or " 1 / 4 ". Returns false, and leaves out alone, for anything else.
    static bool parse(const std::string& text, Rational& out);

private:
    void normalize();

    long num_;
    long den_;
};

Rational operator-(const Rational& r);
Rational operator+(Rational a, const Rational& b);
Rational operator-(Rational a, const Rational& b);
Rational operator*(Rational a, const Rational& b);
Rational operator/(Rational a, const Rational& b);

bool operator==(const Rational& a, const Rational& b);
bool operator!=(const Rational& a, const Rational& b);
bool operator<(const Rational& a, const Rational& b);
bool operator<=(const Rational& a, const Rational& b);

std::ostream& operator<<(std::ostream& os, const Rational& r);

} // namespace calc

#endif
