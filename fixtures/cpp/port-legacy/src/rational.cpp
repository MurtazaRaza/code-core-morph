#include "calc/rational.h"

#include <cstdlib>
#include <ostream>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "calc/strutil.h"

namespace calc {

namespace {

long gcd(long a, long b)
{
    if (a < 0)
        a = -a;
    if (b < 0)
        b = -b;
    while (b != 0) {
        long t = a % b;
        a = b;
        b = t;
    }
    return a;
}

} // namespace

Rational::Rational(long num, long den) : num_(num), den_(den)
{
    if (den_ == 0)
        throw std::domain_error("zero denominator");
    normalize();
}

void Rational::normalize()
{
    if (den_ < 0) {
        num_ = -num_;
        den_ = -den_;
    }
    long g = gcd(num_, den_);
    if (g > 1) {
        num_ /= g;
        den_ /= g;
    }
}

long Rational::truncate() const
{
    return num_ / den_;
}

double Rational::to_double() const
{
    return static_cast<double>(num_) / static_cast<double>(den_);
}

std::string Rational::str() const
{
    std::ostringstream out;
    out << num_;
    if (den_ != 1)
        out << '/' << den_;
    return out.str();
}

Rational& Rational::operator+=(const Rational& other)
{
    num_ = num_ * other.den_ + other.num_ * den_;
    den_ = den_ * other.den_;
    normalize();
    return *this;
}

Rational& Rational::operator-=(const Rational& other)
{
    num_ = num_ * other.den_ - other.num_ * den_;
    den_ = den_ * other.den_;
    normalize();
    return *this;
}

Rational& Rational::operator*=(const Rational& other)
{
    num_ = num_ * other.num_;
    den_ = den_ * other.den_;
    normalize();
    return *this;
}

Rational& Rational::operator/=(const Rational& other)
{
    if (other.num_ == 0)
        throw std::domain_error("division by zero");
    num_ = num_ * other.den_;
    den_ = den_ * other.num_;
    normalize();
    return *this;
}

bool Rational::parse(const std::string& text, Rational& out)
{
    std::vector<std::string> parts = strutil::split(text, '/');
    if (parts.size() > 2)
        return false;
    long values[2] = { 0, 1 };
    for (std::vector<std::string>::size_type i = 0; i < parts.size(); ++i) {
        std::string part = strutil::trim(parts[i]);
        if (part.empty())
            return false;
        char* end = 0;
        values[i] = std::strtol(part.c_str(), &end, 10);
        if (*end != '\0')
            return false;
    }
    if (values[1] == 0)
        return false;
    out = Rational(values[0], values[1]);
    return true;
}

Rational operator-(const Rational& r)
{
    return Rational(-r.num(), r.den());
}

Rational operator+(Rational a, const Rational& b)
{
    return a += b;
}

Rational operator-(Rational a, const Rational& b)
{
    return a -= b;
}

Rational operator*(Rational a, const Rational& b)
{
    return a *= b;
}

Rational operator/(Rational a, const Rational& b)
{
    return a /= b;
}

bool operator==(const Rational& a, const Rational& b)
{
    return a.num() == b.num() && a.den() == b.den();
}

bool operator!=(const Rational& a, const Rational& b)
{
    return !(a == b);
}

bool operator<(const Rational& a, const Rational& b)
{
    return a.num() * b.den() < b.num() * a.den();
}

bool operator<=(const Rational& a, const Rational& b)
{
    return !(b < a);
}

std::ostream& operator<<(std::ostream& os, const Rational& r)
{
    return os << r.str();
}

} // namespace calc
