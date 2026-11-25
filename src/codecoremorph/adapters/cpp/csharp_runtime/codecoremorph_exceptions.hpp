// codecoremorph_exceptions.hpp: C++ exceptions that mirror .NET's, for C++ ports of C# code.
// Written by CodeCoreMorph; do not edit.
//
// Each class names the .NET type it stands for (dotnet_type), so while a port keeps C# façades,
// the bridge can hand the façade what to throw. A project's own exception types derive from
// Exception, override dotnet_type, and list their constructor's arguments in fields().
//
// A DotnetValue is a value as .NET has it (D-97): an ArgumentOutOfRangeException's actual value,
// a log entry's named values. dotnet_value makes one of a number, a bool, a string, an optional
// or a std::chrono::duration (a TimeSpan); a project's own type (an enum) defines
// dotnet_value(const T&) in its namespace, returning its .NET type's full name, its invariant
// data and its text.
#ifndef CODECOREMORPH_EXCEPTIONS_HPP
#define CODECOREMORPH_EXCEPTIONS_HPP

#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace ccm {

// A .NET value as it crosses to C# (D-97): its type's full name ("System.Int32"; empty for
// null) and its invariant data ("5"; a TimeSpan's ticks), which the façades' support reads back
// into that type, and its text as .NET's ToString writes it, where that isn't the data
// ("00:00:01.5000000" for a TimeSpan, an enum's name).
struct DotnetValue {
    std::string type;
    std::string data;
    std::string text;

    bool is_null() const noexcept { return type.empty(); }
    // What .NET's ToString gives (a message's "Actual value was ..."), null as empty.
    const std::string& shown() const noexcept { return text.empty() ? data : text; }
};

// The shortest text that reads back as the same number, as .NET Core writes a double or a
// float: "1.5", "1E+20", "1E-05", "NaN", "Infinity".
template <class F>
std::string floating_text(F value) {
    static_assert(std::is_floating_point_v<F>, "a double or a float");
    if (value != value) return "NaN";
    if (std::isinf(value)) return value > 0 ? "Infinity" : "-Infinity";
    if (value == 0) return std::signbit(value) ? "-0" : "0";
    char buffer[64];
    // As many digits as the shortest text that reads back takes (to_chars)...
    auto result = std::to_chars(buffer, buffer + sizeof buffer, value, std::chars_format::scientific);
    int count = 0;
    for (const char* p = buffer; p != result.ptr && *p != 'e'; ++p) {
        if (*p >= '0' && *p <= '9') ++count;
    }
    // ... and of those, the nearest the value, a tie to the even digit, as .NET picks; printf
    // rounds exactly.
    std::snprintf(buffer, sizeof buffer, "%.*e", count - 1, static_cast<double>(value));
    std::string sci(buffer);
    bool negative = sci.front() == '-';
    std::size_t e = sci.find('e');
    std::string digits;
    for (std::size_t i = negative ? 1 : 0; i < e; ++i) {
        if (sci[i] != '.') digits += sci[i];
    }
    while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
    int exponent = std::atoi(sci.c_str() + e + 1);
    std::string out = negative ? "-" : "";
    // .NET writes it in scientific notation from its round-trip precision (E+09 for a float,
    // E+17 for a double), and below E-04.
    const int scientific = std::is_same_v<F, float> ? 9 : 17;
    if (exponent >= scientific || exponent < -4) {
        out += digits.substr(0, 1);
        if (digits.size() > 1) out += "." + digits.substr(1);
        std::string power = std::to_string(exponent < 0 ? -exponent : exponent);
        if (power.size() < 2) power.insert(0, 2 - power.size(), '0');
        return out + (exponent < 0 ? "E-" : "E+") + power;
    }
    if (exponent < 0) {
        return out + "0." + std::string(static_cast<std::size_t>(-exponent - 1), '0') + digits;
    }
    auto whole = static_cast<std::size_t>(exponent) + 1;
    if (digits.size() <= whole) return out + digits + std::string(whole - digits.size(), '0');
    return out + digits.substr(0, whole) + "." + digits.substr(whole);
}

// A TimeSpan's ticks (100 ns) as its ToString writes them: [-][d.]hh:mm:ss[.fffffff].
inline std::string time_span_text(std::int64_t ticks) {
    std::uint64_t left = ticks < 0 ? 0 - static_cast<std::uint64_t>(ticks) : static_cast<std::uint64_t>(ticks);
    const auto fraction = static_cast<unsigned long long>(left % 10000000U);
    left /= 10000000U;
    const auto seconds = static_cast<unsigned long long>(left % 60U);
    left /= 60U;
    const auto minutes = static_cast<unsigned long long>(left % 60U);
    left /= 60U;
    const auto hours = static_cast<unsigned long long>(left % 24U);
    const auto days = static_cast<unsigned long long>(left / 24U);
    std::string out = ticks < 0 ? "-" : "";
    if (days != 0) out += std::to_string(days) + ".";
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%02llu:%02llu:%02llu", hours, minutes, seconds);
    out += buffer;
    if (fraction != 0) {
        std::snprintf(buffer, sizeof buffer, ".%07llu", fraction);
        out += buffer;
    }
    return out;
}

inline DotnetValue dotnet_value(std::nullptr_t /*value*/) { return {}; }
inline DotnetValue dotnet_value(bool value) {
    return {"System.Boolean", value ? "True" : "False", {}};
}
inline DotnetValue dotnet_value(char value) { return {"System.Char", std::string(1, value), {}}; }
inline DotnetValue dotnet_value(const std::string& value) { return {"System.String", value, {}}; }
inline DotnetValue dotnet_value(std::string_view value) {
    return {"System.String", std::string(value), {}};
}
inline DotnetValue dotnet_value(const char* value) {
    return value == nullptr ? DotnetValue{} : DotnetValue{"System.String", value, {}};
}
inline DotnetValue dotnet_value(double value) {
    return {"System.Double", floating_text(value), {}};
}
inline DotnetValue dotnet_value(float value) { return {"System.Single", floating_text(value), {}}; }

// An integer as the .NET type of its width: int32_t is System.Int32, uint8_t System.Byte.
template <class T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool> &&
                                        !std::is_same_v<T, char>,
                                    int> = 0>
DotnetValue dotnet_value(T value) {
    constexpr bool is_signed = std::is_signed_v<T>;
    const char* type = nullptr;
    if constexpr (sizeof(T) == 1) {
        type = is_signed ? "System.SByte" : "System.Byte";
    } else if constexpr (sizeof(T) == 2) {
        type = is_signed ? "System.Int16" : "System.UInt16";
    } else if constexpr (sizeof(T) == 4) {
        type = is_signed ? "System.Int32" : "System.UInt32";
    } else {
        type = is_signed ? "System.Int64" : "System.UInt64";
    }
    return {type, std::to_string(value), {}};
}

// A duration is a TimeSpan: its ticks, whole ones.
template <class Rep, class Period>
DotnetValue dotnet_value(std::chrono::duration<Rep, Period> value) {
    const std::int64_t ticks =
        std::chrono::duration_cast<std::chrono::duration<std::int64_t, std::ratio<1, 10000000>>>(value)
            .count();
    return {"System.TimeSpan", std::to_string(ticks), time_span_text(ticks)};
}

template <class T>
DotnetValue dotnet_value(const std::optional<T>& value) {
    return value ? dotnet_value(*value) : DotnetValue{};
}

class Exception : public std::runtime_error {
public:
    explicit Exception(const std::string& message = "Exception of type 'System.Exception' was thrown.")
        : std::runtime_error(message) {}
    virtual const char* dotnet_type() const noexcept { return "System.Exception"; }
    // The arguments a façade passes to the .NET exception's constructor, by name.
    virtual std::vector<std::pair<std::string, std::string>> fields() const { return {}; }

    // The C# exception this one stands for, when C# threw it (a call into C#, a C# task): its
    // handle, which the bridge sets and releases. A façade throws that C# exception itself
    // again, so C# catches the object it threw (D-96).
    const std::shared_ptr<void>& managed() const noexcept { return managed_; }
    void set_managed(std::shared_ptr<void> managed) noexcept { managed_ = std::move(managed); }

    // For the bridge, an OperationCanceledException's token (D-97): none where there is none
    // that can be canceled; else the C# token it stands for, null for one native code made.
    virtual std::optional<std::shared_ptr<void>> token_origin() const { return std::nullopt; }

private:
    std::shared_ptr<void> managed_;
};

// A .NET exception's mirror: Base may be qualified (ccm::Exception) in a port's own namespace.
#define CCM_MIRROR_EXCEPTION(Name, Base, DotnetType)                                      \
    class Name : public Base {                                                             \
        using ccm_base_ = Base;                                                            \
                                                                                           \
    public:                                                                                \
        using ccm_base_::ccm_base_;                                                        \
        const char* dotnet_type() const noexcept override { return DotnetType; }           \
    }

class ArgumentException : public Exception {
public:
    explicit ArgumentException(const std::string& message = {}, std::string param_name = {})
        : Exception(message), param_name_(std::move(param_name)) {}
    const char* dotnet_type() const noexcept override { return "System.ArgumentException"; }
    const std::string& param_name() const noexcept { return param_name_; }

private:
    std::string param_name_;
};

// As .NET's: the parameter's name comes first.
class ArgumentNullException : public ArgumentException {
public:
    explicit ArgumentNullException(std::string param_name = {}, const std::string& message = {})
        : ArgumentException(message, std::move(param_name)) {}
    const char* dotnet_type() const noexcept override { return "System.ArgumentNullException"; }
};

class ArgumentOutOfRangeException : public ArgumentException {
public:
    explicit ArgumentOutOfRangeException(std::string param_name = {}, const std::string& message = {})
        : ArgumentException(message, std::move(param_name)) {}
    // ArgumentOutOfRangeException(paramName, actualValue, message): the façade's exception
    // reports the value as its ActualValue, and its message says it (D-97).
    ArgumentOutOfRangeException(std::string param_name, DotnetValue actual_value,
                                const std::string& message)
        : ArgumentException(message, std::move(param_name)), actual_value_(std::move(actual_value)) {}
    template <class T, std::enable_if_t<!std::is_convertible_v<const T&, DotnetValue>, int> = 0>
    ArgumentOutOfRangeException(std::string param_name, const T& actual_value,
                                const std::string& message)
        : ArgumentOutOfRangeException(std::move(param_name), dotnet_value(actual_value), message) {}
    const char* dotnet_type() const noexcept override {
        return "System.ArgumentOutOfRangeException";
    }
    // The actual value, or a null one.
    const DotnetValue& actual_value() const noexcept { return actual_value_; }

private:
    DotnetValue actual_value_;
};

CCM_MIRROR_EXCEPTION(InvalidOperationException, Exception, "System.InvalidOperationException");
CCM_MIRROR_EXCEPTION(ObjectDisposedException, InvalidOperationException,
                     "System.ObjectDisposedException");
CCM_MIRROR_EXCEPTION(NotSupportedException, Exception, "System.NotSupportedException");
CCM_MIRROR_EXCEPTION(NotImplementedException, Exception, "System.NotImplementedException");
CCM_MIRROR_EXCEPTION(KeyNotFoundException, Exception,
                     "System.Collections.Generic.KeyNotFoundException");
CCM_MIRROR_EXCEPTION(InvalidDataException, Exception, "System.IO.InvalidDataException");
CCM_MIRROR_EXCEPTION(IndexOutOfRangeException, Exception, "System.IndexOutOfRangeException");
CCM_MIRROR_EXCEPTION(NullReferenceException, Exception, "System.NullReferenceException");
CCM_MIRROR_EXCEPTION(FormatException, Exception, "System.FormatException");
CCM_MIRROR_EXCEPTION(ArithmeticException, Exception, "System.ArithmeticException");
CCM_MIRROR_EXCEPTION(OverflowException, ArithmeticException, "System.OverflowException");
CCM_MIRROR_EXCEPTION(DivideByZeroException, ArithmeticException, "System.DivideByZeroException");
// What C#'s runtime binder throws where a dynamic site binds nothing for the value's type
// (D-88). The façade's project maps it (CcmExceptions.cs): only a project that uses dynamic
// references Microsoft.CSharp.
CCM_MIRROR_EXCEPTION(RuntimeBinderException, Exception,
                     "Microsoft.CSharp.RuntimeBinder.RuntimeBinderException");

}  // namespace ccm

#endif
