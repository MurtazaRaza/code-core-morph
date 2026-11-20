// codecoremorph_bridge.hpp: helpers for the C++ side of a Java façade's bridge. Written by
// CodeCoreMorph; do not edit. Only bridge files include it.
#ifndef CODECOREMORPH_BRIDGE_HPP
#define CODECOREMORPH_BRIDGE_HPP

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <new>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "codecoremorph_bridge.h"
#include "codecoremorph_exceptions.hpp"

namespace ccm::bridge {

// Thrown through C++ when a call into Java threw; the façade rethrows the Java exception.
class ManagedException : public ccm::Exception {
public:
    ManagedException() : ccm::Exception("a call into Java threw") {}
    const char* java_type() const noexcept override { return "codecoremorph.Managed"; }
};

ccm_status current_exception_status() noexcept;

// Runs an export's body: no exception leaves it.
template <class F>
ccm_status guard(F&& body) noexcept {
    try {
        std::forward<F>(body)();
        return CCM_OK;
    } catch (...) {
        return current_exception_status();
    }
}

// What a call into Java returned: anything but CCM_OK becomes a ManagedException.
inline void check_managed(ccm_status status) {
    if (status != CCM_OK) throw ManagedException();
}

// --- handles: a heap-allocated std::shared_ptr to the object ---------------------------------

template <class Handle>
using target_t = typename handle_traits<Handle>::type;

template <class Handle, class T>
Handle* to_handle(std::shared_ptr<T> object) {
    if (!object) return nullptr;
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory): the handle owns it; release frees it.
    return reinterpret_cast<Handle*>(new std::shared_ptr<target_t<Handle>>(std::move(object)));
}

// The object a handle holds. A null handle is Java's null: using it throws
// NullPointerException.
template <class Handle>
const std::shared_ptr<target_t<Handle>>& from_handle(const Handle* handle) {
    if (handle == nullptr) throw ccm::NullPointerException();
    return *reinterpret_cast<const std::shared_ptr<target_t<Handle>>*>(handle);
}

template <class Handle>
std::shared_ptr<target_t<Handle>> from_handle_or_null(const Handle* handle) {
    return handle == nullptr ? nullptr : from_handle(handle);
}

template <class Handle>
void release(Handle* handle) {
    delete reinterpret_cast<std::shared_ptr<target_t<Handle>>*>(handle);  // NOLINT
}

template <class Handle>
std::uint64_t key(const Handle* handle) {
    if (handle == nullptr) return 0;
    const target_t<Handle>* object = from_handle(handle).get();
    if constexpr (std::is_polymorphic_v<target_t<Handle>>) {
        return reinterpret_cast<std::uintptr_t>(dynamic_cast<const void*>(object));
    } else {
        return reinterpret_cast<std::uintptr_t>(object);
    }
}

// --- strings: UTF-8 in, UTF-8 out ------------------------------------------------------------

// A string the Java dereferences: null throws NullPointerException (with the parameter's name
// as its message when the Java's own check gave it that, e.g. Objects.requireNonNull(x, "x")).
inline std::string str(const char* text, const char* message = nullptr) {
    if (text == nullptr) throw ccm::NullPointerException(message ? message : "");
    return text;
}

inline std::optional<std::string> opt_str(const char* text) {
    return text == nullptr ? std::nullopt : std::optional<std::string>(text);
}

inline char* dup(const std::string& text) {
    auto* copy = static_cast<char*>(std::malloc(text.size() + 1));  // NOLINT
    if (copy == nullptr) throw std::bad_alloc();
    std::memcpy(copy, text.c_str(), text.size() + 1);
    return copy;
}

inline void out_str(char** out, const std::string& text) { *out = dup(text); }

inline void out_opt_str(char** out, const std::optional<std::string>& text) {
    *out = text ? dup(*text) : nullptr;
}

// A string a call into Java returned, freed once read; null for null.
inline std::optional<std::string> take_opt_str(char* text) {
    if (text == nullptr) return std::nullopt;
    std::string copy(text);
    std::free(text);  // NOLINT(cppcoreguidelines-no-malloc)
    return copy;
}

inline std::string take_str(char* text) {
    auto s = take_opt_str(text);
    if (!s) throw ccm::NullPointerException();
    return *s;
}

// --- arrays ----------------------------------------------------------------------------------

template <class T>
std::vector<T> array(const T* data, int32_t count, const char* message = nullptr) {
    if (count < 0) throw ccm::NullPointerException(message ? message : "");
    return count == 0 ? std::vector<T>() : std::vector<T>(data, data + count);
}

template <class T>
void out_array(T** out, int32_t* count, const std::vector<T>& values) {
    static_assert(std::is_trivially_copyable_v<T>, "an array out is of numbers");
    if (values.size() > static_cast<std::size_t>(INT32_MAX)) throw std::length_error("array");
    auto* copy = static_cast<T*>(std::malloc(values.empty() ? 1 : values.size() * sizeof(T)));  // NOLINT
    if (copy == nullptr) throw std::bad_alloc();
    if (!values.empty()) std::memcpy(copy, values.data(), values.size() * sizeof(T));
    *out = copy;
    *count = static_cast<int32_t>(values.size());
}

// --- calls into Java -------------------------------------------------------------------------

// A Java object held by native code (a JNI global reference), released when the last copy
// goes.
class ManagedRef {
public:
    ManagedRef(void* context, ccm_release_fn release)
        : context_(context, [release](void* c) {
              if (release != nullptr && c != nullptr) release(c);
          }) {}
    void* get() const { return context_.get(); }

private:
    std::shared_ptr<void> context_;
};

}  // namespace ccm::bridge

#define CCM_HANDLE_EXPORTS(Handle)                                                \
    extern "C" CCM_EXPORT void Handle##_release(Handle* handle) {                 \
        ::ccm::bridge::release(handle);                                           \
    }                                                                             \
    extern "C" CCM_EXPORT std::uint64_t Handle##_key(const Handle* handle) {      \
        return ::ccm::bridge::key(handle);                                        \
    }

#endif
