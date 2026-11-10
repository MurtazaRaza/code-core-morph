// codecoremorph_exceptions.hpp: C++ exceptions that mirror .NET's, for C++ ports of C# code.
// Written by CodeCoreMorph; do not edit.
//
// Each class names the .NET type it stands for (dotnet_type), so while a port keeps C# façades,
// the bridge can hand the façade what to throw. A project's own exception types derive from
// Exception, override dotnet_type, and list their constructor's arguments in fields().
#ifndef CODECOREMORPH_EXCEPTIONS_HPP
#define CODECOREMORPH_EXCEPTIONS_HPP

#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace ccm {

class Exception : public std::runtime_error {
public:
    explicit Exception(const std::string& message = "Exception of type 'System.Exception' was thrown.")
        : std::runtime_error(message) {}
    virtual const char* dotnet_type() const noexcept { return "System.Exception"; }
    // The arguments a façade passes to the .NET exception's constructor, by name.
    virtual std::vector<std::pair<std::string, std::string>> fields() const { return {}; }
};

#define CCM_MIRROR_EXCEPTION(Name, Base, DotnetType)                                      \
    class Name : public Base {                                                             \
    public:                                                                                \
        using Base::Base;                                                                  \
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
    const char* dotnet_type() const noexcept override {
        return "System.ArgumentOutOfRangeException";
    }
};

CCM_MIRROR_EXCEPTION(InvalidOperationException, Exception, "System.InvalidOperationException");
CCM_MIRROR_EXCEPTION(ObjectDisposedException, InvalidOperationException,
                     "System.ObjectDisposedException");
CCM_MIRROR_EXCEPTION(NotSupportedException, Exception, "System.NotSupportedException");
CCM_MIRROR_EXCEPTION(NotImplementedException, Exception, "System.NotImplementedException");
CCM_MIRROR_EXCEPTION(KeyNotFoundException, Exception,
                     "System.Collections.Generic.KeyNotFoundException");
CCM_MIRROR_EXCEPTION(IndexOutOfRangeException, Exception, "System.IndexOutOfRangeException");
CCM_MIRROR_EXCEPTION(NullReferenceException, Exception, "System.NullReferenceException");
CCM_MIRROR_EXCEPTION(FormatException, Exception, "System.FormatException");
CCM_MIRROR_EXCEPTION(ArithmeticException, Exception, "System.ArithmeticException");
CCM_MIRROR_EXCEPTION(OverflowException, ArithmeticException, "System.OverflowException");
CCM_MIRROR_EXCEPTION(DivideByZeroException, ArithmeticException, "System.DivideByZeroException");

}  // namespace ccm

#endif
