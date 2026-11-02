#ifndef META_UNITS_H
#define META_UNITS_H

#include <string>
#include <type_traits>

namespace meta {

// Compile-time unit tags.
struct Meters {};
struct Seconds {};
struct MetersPerSecond {};

template <typename Unit>
struct unit_name;

template <>
struct unit_name<Meters> {
    static const char* get() { return "m"; }
};

template <>
struct unit_name<Seconds> {
    static const char* get() { return "s"; }
};

template <>
struct unit_name<MetersPerSecond> {
    static const char* get() { return "m/s"; }
};

// A strongly typed measurement. Arithmetic is only allowed between equal units.
template <typename Unit>
class Measure {
public:
    explicit Measure(double value) : value_(value) {}
    double value() const { return value_; }

    Measure operator+(const Measure& other) const { return Measure(value_ + other.value_); }
    Measure operator-(const Measure& other) const { return Measure(value_ - other.value_); }
    bool operator<(const Measure& other) const { return value_ < other.value_; }

    std::string str() const;

private:
    double value_;
};

// Division of distance by time gives a speed; nothing else divides.
template <typename A, typename B>
typename std::enable_if<std::is_same<A, Meters>::value && std::is_same<B, Seconds>::value,
                        Measure<MetersPerSecond> >::type
operator/(const Measure<A>& a, const Measure<B>& b) {
    return Measure<MetersPerSecond>(a.value() / b.value());
}

std::string formatMeasure(double value, const char* unit);

template <typename Unit>
std::string Measure<Unit>::str() const {
    return formatMeasure(value_, unit_name<Unit>::get());
}

}  // namespace meta

#endif  // META_UNITS_H
