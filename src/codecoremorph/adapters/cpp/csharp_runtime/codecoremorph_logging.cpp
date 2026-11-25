// codecoremorph_logging.cpp: C# loggers behind ccm::logging's interfaces (D-95). Written by
// CodeCoreMorph; do not edit. The retirement deletes it with the bridge.
#include "codecoremorph_logging.h"

#include <atomic>
#include <exception>
#include <string>
#include <utility>
#include <vector>

#include "codecoremorph_bridge.hpp"

namespace {

std::atomic<ccm_log_enabled_fn> enabled_fn{nullptr};
std::atomic<ccm_log_fn> log_fn{nullptr};
std::atomic<ccm_create_logger_fn> create_fn{nullptr};
std::atomic<ccm_release_fn> release_fn{nullptr};

template <class F>
F registered(const std::atomic<F> &fn) {
    F f = fn.load();
    if (f == nullptr) throw ccm::InvalidOperationException("the façades' logging support isn't set up");
    return f;
}

class ManagedLogger final : public ccm::logging::ILogger {
public:
    explicit ManagedLogger(ccm::bridge::ManagedRef managed) : managed_(std::move(managed)) {}

    bool is_enabled(ccm::logging::LogLevel level) const override {
        bool enabled = false;
        ccm::bridge::check_managed(
            registered(enabled_fn)(managed_.get(), static_cast<int32_t>(level), &enabled));
        return enabled;
    }

    // A message logged without a template: .NET's state is the message itself.
    void log(ccm::logging::LogLevel level, const ccm::logging::EventId &event,
             const std::string &message, const std::exception *exception) override {
        send(level, event, message, nullptr, {}, exception);
    }

    void log_state(ccm::logging::LogLevel level, const ccm::logging::EventId &event,
                   const ccm::logging::LogState &state, const std::exception *exception) override {
        std::vector<ccm_log_value> values;
        values.reserve(state.values.size());
        for (const auto &[name, value] : state.values) {
            values.push_back(ccm_log_value{name.c_str(),
                                           value.is_null() ? nullptr : value.type.c_str(),
                                           value.data.c_str()});
        }
        send(level, event, state.message, state.message_template.c_str(), values, exception);
    }

private:
    void send(ccm::logging::LogLevel level, const ccm::logging::EventId &event,
              const std::string &message, const char *message_template,
              const std::vector<ccm_log_value> &values, const std::exception *exception) const {
        ccm_log_fn call = registered(log_fn);
        // The exception's record, read by the support during the call: a C# exception is the
        // object C# threw.
        ccm_status thrown = exception != nullptr ? ccm::bridge::record_exception(*exception) : CCM_OK;
        ccm::bridge::check_managed(call(managed_.get(), static_cast<int32_t>(level), event.id,
                                        event.name ? event.name->c_str() : nullptr,
                                        message.c_str(), message_template,
                                        values.empty() ? nullptr : values.data(),
                                        static_cast<int32_t>(values.size()), thrown));
    }

    ccm::bridge::ManagedRef managed_;
};

class ManagedLoggerFactory final : public ccm::logging::ILoggerFactory {
public:
    explicit ManagedLoggerFactory(ccm::bridge::ManagedRef managed) : managed_(std::move(managed)) {}

    std::shared_ptr<ccm::logging::ILogger> create_logger(const std::string &category) override {
        void *made = nullptr;
        ccm::bridge::check_managed(registered(create_fn)(managed_.get(), category.c_str(), &made));
        return ccm::bridge::logger(made);
    }

private:
    ccm::bridge::ManagedRef managed_;
};

}  // namespace

extern "C" CCM_EXPORT void ccm_logging_init(ccm_log_enabled_fn enabled, ccm_log_fn log,
                                            ccm_create_logger_fn create, ccm_release_fn release) {
    enabled_fn.store(enabled);
    log_fn.store(log);
    create_fn.store(create);
    release_fn.store(release);
}

namespace ccm::bridge {

// The context is taken first, so it is released however what follows fails.
std::shared_ptr<ccm::logging::ILogger> logger(void *context) {
    if (context == nullptr) return nullptr;
    ManagedRef managed(context, release_fn.load());
    return std::make_shared<ManagedLogger>(std::move(managed));
}

std::shared_ptr<ccm::logging::ILoggerFactory> logger_factory(void *context) {
    if (context == nullptr) return nullptr;
    ManagedRef managed(context, release_fn.load());
    return std::make_shared<ManagedLoggerFactory>(std::move(managed));
}

}  // namespace ccm::bridge
