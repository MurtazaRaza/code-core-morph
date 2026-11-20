// codecoremorph_exceptions.hpp: C++ exceptions that mirror Java's, for C++ ports of Java code.
// Written by CodeCoreMorph; do not edit.
//
// Each class names the Java type it stands for (java_type), so while a port keeps Java façades,
// the bridge can hand the façade what to throw. A project's own exception types derive from
// Exception (or the mirror of their Java base), override java_type, and list their
// constructor's arguments in fields().
#ifndef CODECOREMORPH_EXCEPTIONS_HPP
#define CODECOREMORPH_EXCEPTIONS_HPP

#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace ccm {

// java.lang.Throwable's message is null unless given one: an empty message is null.
class Exception : public std::runtime_error {
public:
    explicit Exception(const std::string& message = {}) : std::runtime_error(message) {}
    virtual const char* java_type() const noexcept { return "java.lang.Exception"; }
    // The arguments a façade passes to the Java exception's constructor, by name.
    virtual std::vector<std::pair<std::string, std::string>> fields() const { return {}; }
};

#define CCM_MIRROR_EXCEPTION(Name, Base, JavaType)                                         \
    class Name : public Base {                                                             \
    public:                                                                                \
        using Base::Base;                                                                  \
        const char* java_type() const noexcept override { return JavaType; }               \
    }

CCM_MIRROR_EXCEPTION(RuntimeException, Exception, "java.lang.RuntimeException");
CCM_MIRROR_EXCEPTION(IllegalArgumentException, RuntimeException,
                     "java.lang.IllegalArgumentException");
CCM_MIRROR_EXCEPTION(NumberFormatException, IllegalArgumentException,
                     "java.lang.NumberFormatException");
CCM_MIRROR_EXCEPTION(IllegalStateException, RuntimeException, "java.lang.IllegalStateException");
CCM_MIRROR_EXCEPTION(NullPointerException, RuntimeException, "java.lang.NullPointerException");
CCM_MIRROR_EXCEPTION(ArithmeticException, RuntimeException, "java.lang.ArithmeticException");
CCM_MIRROR_EXCEPTION(ClassCastException, RuntimeException, "java.lang.ClassCastException");
CCM_MIRROR_EXCEPTION(IndexOutOfBoundsException, RuntimeException,
                     "java.lang.IndexOutOfBoundsException");
CCM_MIRROR_EXCEPTION(ArrayIndexOutOfBoundsException, IndexOutOfBoundsException,
                     "java.lang.ArrayIndexOutOfBoundsException");
CCM_MIRROR_EXCEPTION(StringIndexOutOfBoundsException, IndexOutOfBoundsException,
                     "java.lang.StringIndexOutOfBoundsException");
CCM_MIRROR_EXCEPTION(NegativeArraySizeException, RuntimeException,
                     "java.lang.NegativeArraySizeException");
CCM_MIRROR_EXCEPTION(UnsupportedOperationException, RuntimeException,
                     "java.lang.UnsupportedOperationException");
CCM_MIRROR_EXCEPTION(NoSuchElementException, RuntimeException, "java.util.NoSuchElementException");
CCM_MIRROR_EXCEPTION(ConcurrentModificationException, RuntimeException,
                     "java.util.ConcurrentModificationException");
CCM_MIRROR_EXCEPTION(EmptyStackException, RuntimeException, "java.util.EmptyStackException");
CCM_MIRROR_EXCEPTION(CloneNotSupportedException, Exception, "java.lang.CloneNotSupportedException");
CCM_MIRROR_EXCEPTION(InterruptedException, Exception, "java.lang.InterruptedException");
CCM_MIRROR_EXCEPTION(IOException, Exception, "java.io.IOException");
CCM_MIRROR_EXCEPTION(UncheckedIOException, RuntimeException, "java.io.UncheckedIOException");

}  // namespace ccm

#endif
