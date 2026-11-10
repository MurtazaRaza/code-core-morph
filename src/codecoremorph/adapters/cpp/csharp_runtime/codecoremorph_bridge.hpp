// codecoremorph_bridge.hpp: helpers for the C++ side of a C# façade's bridge. Written by
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
#include "codecoremorph_decimal.hpp"
#include "codecoremorph_exceptions.hpp"

namespace ccm::bridge {

// Thrown through C++ when a call into C# threw; the façade rethrows the C# exception itself.
class ManagedException : public ccm::Exception {
public:
    ManagedException() : ccm::Exception("a call into .NET threw") {}
    const char* dotnet_type() const noexcept override { return "CodeCoreMorph.Managed"; }
};

// The status of the exception being handled, its details kept for ccm_error_part.
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

// What a call into C# returned: CCM_MANAGED becomes a ManagedException.
inline void check_managed(ccm_status status) {
    if (status != CCM_OK) throw ManagedException();
}

// --- handles: a handle is a heap-allocated std::shared_ptr to the object --------------------

template <class Handle>
using target_t = typename handle_traits<Handle>::type;

// A new handle, owned by the receiver, to a shared object; null for an empty pointer.
template <class Handle, class T>
Handle* to_handle(std::shared_ptr<T> object) {
    if (!object) return nullptr;
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory): the handle owns it; release frees it.
    return reinterpret_cast<Handle*>(new std::shared_ptr<target_t<Handle>>(std::move(object)));
}

// The object a handle holds. A null handle is C#'s null: dereferencing it throws.
template <class Handle>
const std::shared_ptr<target_t<Handle>>& from_handle(const Handle* handle) {
    if (handle == nullptr) {
        throw ccm::NullReferenceException("Object reference not set to an instance of an object.");
    }
    return *reinterpret_cast<const std::shared_ptr<target_t<Handle>>*>(handle);
}

// The object, or an empty pointer for null, where C# allows null.
template <class Handle>
std::shared_ptr<target_t<Handle>> from_handle_or_null(const Handle* handle) {
    return handle == nullptr ? nullptr : from_handle(handle);
}

template <class Handle>
void release(Handle* handle) {
    delete reinterpret_cast<std::shared_ptr<target_t<Handle>>*>(handle);  // NOLINT
}

// The object's identity: the address of its most derived object, whatever the handle's type.
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

// --- strings: UTF-8 in, UTF-8 out (freed by the façade with ccm_free) ------------------------

// A string parameter C# requires: null throws ArgumentNullException, as the C# check did.
inline std::string str(const char* text, const char* param_name) {
    if (text == nullptr) throw ccm::ArgumentNullException(param_name);
    return text;
}

// A string parameter that may be null.
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

// --- arrays: in as a pointer and a count, out as a copy the façade frees (D-82) --------------

// An array parameter C# requires: the façade passes a count of -1 for null, which throws
// ArgumentNullException, as the C# check did.
template <class T>
std::vector<T> array(const T* data, int32_t count, const char* param_name) {
    if (count < 0) throw ccm::ArgumentNullException(param_name);
    return count == 0 ? std::vector<T>() : std::vector<T>(data, data + count);
}

// An array the export returns: a malloc'd copy and its length, which the façade reads and
// frees (CcmInterop.TakeBytes for bytes); never null, so an empty array is one too.
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

// --- decimals --------------------------------------------------------------------------------

inline ccm::Decimal dec(ccm_decimal d) {
    return ccm::Decimal::from_bits(d.lo, d.mid, d.hi, d.flags);
}

inline ccm_decimal dec(const ccm::Decimal& d) {
    auto b = d.bits();
    return ccm_decimal{b[0], b[1], b[2], b[3]};
}

// --- calls into C# -----------------------------------------------------------------------------

// A C# object held by native code (a GCHandle), released when the last copy goes.
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

// Defines a handle type's release and key exports (in its bridge file).
#define CCM_HANDLE_EXPORTS(Handle)                                                \
    extern "C" CCM_EXPORT void Handle##_release(Handle* handle) {                 \
        ::ccm::bridge::release(handle);                                           \
    }                                                                             \
    extern "C" CCM_EXPORT std::uint64_t Handle##_key(const Handle* handle) {      \
        return ::ccm::bridge::key(handle);                                        \
    }

#endif
