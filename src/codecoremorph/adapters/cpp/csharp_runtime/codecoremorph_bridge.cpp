// codecoremorph_bridge.cpp: the bridge's error record and string functions. Written by
// CodeCoreMorph; do not edit.
#include "codecoremorph_bridge.hpp"

#include <exception>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>

namespace {

thread_local std::string error_type;
thread_local std::string error_message;
thread_local std::string error_param;
thread_local std::string error_fields;
// An ArgumentOutOfRangeException's actual value: its .NET type and data (D-97).
thread_local std::string error_actual_type;
thread_local std::string error_actual_data;
// An OperationCanceledException's token (D-97): whether it has one, and the C# token it stands
// for (null for one native code made).
thread_local bool error_has_token = false;
thread_local std::shared_ptr<void> error_token;
// The C# exception the thread's last export failed with (CCM_MANAGED), kept until its next.
thread_local std::shared_ptr<void> error_managed;

void set_error(const char* type, const char* message, const std::string& param,
               const std::string& fields) {
    error_type = type;
    error_message = message;
    error_param = param;
    error_fields = fields;
    error_actual_type.clear();
    error_actual_data.clear();
    error_has_token = false;
    error_token.reset();
    error_managed.reset();
}

std::string format_fields(const ccm::Exception& e) {
    std::string out;
    for (const auto& [name, value] : e.fields()) {
        out += name;
        out += '=';
        for (char c : value) {
            if (c == '\\') {
                out += "\\\\";
            } else if (c == '\n') {
                out += "\\n";
            } else {
                out += c;
            }
        }
        out += '\n';
    }
    return out;
}

// --- C# exceptions by type (D-96) ---------------------------------------------------------------

template <class E>
std::exception_ptr argument(const std::string& message, std::shared_ptr<void> managed) {
    E error({}, message);  // the parameter's name is the C# exception's, which the façade throws
    error.set_managed(std::move(managed));
    return std::make_exception_ptr(std::move(error));
}

std::mutex& registry_mutex() {
    static std::mutex mutex;
    return mutex;
}

std::map<std::string, ccm::bridge::ExceptionFactory>& registry() {
    using ccm::bridge::make_mirror;
    static std::map<std::string, ccm::bridge::ExceptionFactory> known{
        {"System.Exception", &make_mirror<ccm::Exception>},
        {"System.ArgumentException",
         [](const std::string& m, std::shared_ptr<void> h) {
             ccm::ArgumentException error(m);
             error.set_managed(std::move(h));
             return std::make_exception_ptr(std::move(error));
         }},
        {"System.ArgumentNullException", &argument<ccm::ArgumentNullException>},
        {"System.ArgumentOutOfRangeException", &argument<ccm::ArgumentOutOfRangeException>},
        {"System.InvalidOperationException", &make_mirror<ccm::InvalidOperationException>},
        {"System.ObjectDisposedException", &make_mirror<ccm::ObjectDisposedException>},
        {"System.NotSupportedException", &make_mirror<ccm::NotSupportedException>},
        {"System.NotImplementedException", &make_mirror<ccm::NotImplementedException>},
        {"System.Collections.Generic.KeyNotFoundException", &make_mirror<ccm::KeyNotFoundException>},
        {"System.IO.InvalidDataException", &make_mirror<ccm::InvalidDataException>},
        {"System.IndexOutOfRangeException", &make_mirror<ccm::IndexOutOfRangeException>},
        {"System.NullReferenceException", &make_mirror<ccm::NullReferenceException>},
        {"System.FormatException", &make_mirror<ccm::FormatException>},
        {"System.ArithmeticException", &make_mirror<ccm::ArithmeticException>},
        {"System.OverflowException", &make_mirror<ccm::OverflowException>},
        {"System.DivideByZeroException", &make_mirror<ccm::DivideByZeroException>},
    };
    return known;
}

struct Recorded {
    std::string types;
    std::string message;
    std::shared_ptr<void> exception;
};

thread_local std::optional<Recorded> recorded;

}  // namespace

bool ccm::bridge::register_exception(const char* dotnet_type, ExceptionFactory factory) {
    std::lock_guard<std::mutex> lock(registry_mutex());
    registry()[dotnet_type] = factory;
    return true;
}

std::exception_ptr ccm::bridge::take_managed() {
    if (!recorded) return std::make_exception_ptr(ManagedException());
    Recorded taken = std::move(*recorded);
    recorded.reset();
    std::string::size_type start = 0;
    while (start <= taken.types.size()) {
        std::string::size_type end = taken.types.find('\n', start);
        std::string type = taken.types.substr(start, end == std::string::npos ? end : end - start);
        ExceptionFactory factory = nullptr;
        {
            std::lock_guard<std::mutex> lock(registry_mutex());
            auto found = registry().find(type);
            if (found != registry().end()) factory = found->second;
        }
        if (factory != nullptr) return factory(taken.message, taken.exception);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    ManagedException error(taken.message);
    error.set_managed(taken.exception);
    return std::make_exception_ptr(std::move(error));
}

ccm_status ccm::bridge::record_exception(const std::exception& error) noexcept {
    try {
        if (const auto* e = dynamic_cast<const ccm::Exception*>(&error)) {
            // A C# exception passing through: the façade throws that object again, by its
            // handle, or, without one, the exception its support kept pending.
            if (e->managed() || dynamic_cast<const ManagedException*>(e) != nullptr) {
                error_managed = e->managed();
                return CCM_MANAGED;
            }
            const auto* argument_error = dynamic_cast<const ccm::ArgumentException*>(e);
            set_error(e->dotnet_type(), e->what(),
                      argument_error != nullptr ? argument_error->param_name() : std::string(),
                      format_fields(*e));
            if (const auto* range = dynamic_cast<const ccm::ArgumentOutOfRangeException*>(e)) {
                error_actual_type = range->actual_value().type;
                error_actual_data = range->actual_value().data;
            }
            if (std::optional<std::shared_ptr<void>> origin = e->token_origin()) {
                error_has_token = true;
                error_token = std::move(*origin);
            }
        } else if (dynamic_cast<const std::bad_alloc*>(&error) != nullptr) {
            set_error("System.OutOfMemoryException", error.what(), {}, {});
        } else if (dynamic_cast<const std::out_of_range*>(&error) != nullptr) {
            set_error("System.ArgumentOutOfRangeException", error.what(), {}, {});
        } else if (dynamic_cast<const std::invalid_argument*>(&error) != nullptr) {
            set_error("System.ArgumentException", error.what(), {}, {});
        } else if (dynamic_cast<const std::overflow_error*>(&error) != nullptr) {
            set_error("System.OverflowException", error.what(), {}, {});
        } else if (dynamic_cast<const std::logic_error*>(&error) != nullptr) {
            set_error("System.InvalidOperationException", error.what(), {}, {});
        } else {
            set_error("System.Exception", error.what(), {}, {});
        }
    } catch (...) {
        set_error("System.OutOfMemoryException", "the bridge couldn't record an exception", {}, {});
    }
    return CCM_ERROR;
}

ccm_status ccm::bridge::current_exception_status() noexcept {
    try {
        throw;
    } catch (const std::exception& e) {
        return record_exception(e);
    } catch (...) {
        set_error("System.Exception", "an unknown C++ exception", {}, {});
    }
    return CCM_ERROR;
}

extern "C" {

CCM_EXPORT const char* ccm_error_part(int32_t part) {
    switch (part) {
        case 0: return error_type.c_str();
        case 1: return error_message.c_str();
        case 2: return error_param.c_str();
        case 3: return error_fields.c_str();
        case 4: return error_actual_type.c_str();
        case 5: return error_actual_data.c_str();
        default: return "";
    }
}

CCM_EXPORT bool ccm_error_token(void** origin) {
    if (origin != nullptr) *origin = error_token.get();
    return error_has_token;
}

CCM_EXPORT void ccm_free(void* memory) { std::free(memory); }  // NOLINT

CCM_EXPORT char* ccm_strdup(const char* text) {
    try {
        return text == nullptr ? nullptr : ccm::bridge::dup(text);
    } catch (...) {
        return nullptr;
    }
}

CCM_EXPORT bool ccm_director_shared(const void* director) {
    return director != nullptr && static_cast<const ccm::bridge::Director*>(director)->shared();
}

CCM_EXPORT void ccm_set_managed(const char* types, const char* message, void* exception,
                                ccm_release_fn release) {
    try {
        recorded = Recorded{types != nullptr ? types : "", message != nullptr ? message : "",
                            ccm::bridge::ManagedRef(exception, release).shared()};
    } catch (...) {
        if (release != nullptr && exception != nullptr) release(exception);
    }
}

CCM_EXPORT void* ccm_error_managed(void) { return error_managed.get(); }

}  // extern "C"
