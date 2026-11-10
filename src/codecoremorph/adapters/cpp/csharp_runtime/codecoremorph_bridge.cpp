// codecoremorph_bridge.cpp: the bridge's error record and string functions. Written by
// CodeCoreMorph; do not edit.
#include "codecoremorph_bridge.hpp"

#include <exception>
#include <stdexcept>
#include <string>

namespace {

thread_local std::string error_type;
thread_local std::string error_message;
thread_local std::string error_param;
thread_local std::string error_fields;

void set_error(const char* type, const char* message, const std::string& param,
               const std::string& fields) {
    error_type = type;
    error_message = message;
    error_param = param;
    error_fields = fields;
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

}  // namespace

ccm_status ccm::bridge::current_exception_status() noexcept {
    try {
        throw;
    } catch (const ManagedException&) {
        return CCM_MANAGED;
    } catch (const ccm::ArgumentException& e) {
        set_error(e.dotnet_type(), e.what(), e.param_name(), format_fields(e));
    } catch (const ccm::Exception& e) {
        set_error(e.dotnet_type(), e.what(), {}, format_fields(e));
    } catch (const std::bad_alloc& e) {
        set_error("System.OutOfMemoryException", e.what(), {}, {});
    } catch (const std::out_of_range& e) {
        set_error("System.ArgumentOutOfRangeException", e.what(), {}, {});
    } catch (const std::invalid_argument& e) {
        set_error("System.ArgumentException", e.what(), {}, {});
    } catch (const std::overflow_error& e) {
        set_error("System.OverflowException", e.what(), {}, {});
    } catch (const std::logic_error& e) {
        set_error("System.InvalidOperationException", e.what(), {}, {});
    } catch (const std::exception& e) {
        set_error("System.Exception", e.what(), {}, {});
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
        default: return error_fields.c_str();
    }
}

CCM_EXPORT void ccm_free(void* memory) { std::free(memory); }  // NOLINT

CCM_EXPORT char* ccm_strdup(const char* text) {
    try {
        return text == nullptr ? nullptr : ccm::bridge::dup(text);
    } catch (...) {
        return nullptr;
    }
}

}  // extern "C"
