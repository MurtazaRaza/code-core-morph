// A check of the C++ runtime's M13 fixes (D-97), outside any port: the DotnetValue of each kind
// of value, the ArgumentOutOfRangeException's actual value, a log entry's state, and the token a
// cancellation carries, through the bridge's error record as a façade reads it.
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>

#include "codecoremorph_bridge.hpp"
#include "codecoremorph_logging.h"
#include "codecoremorph_tasks.h"

namespace shop {
enum class Backoff { Constant = 0, Exponential = 2 };
inline ccm::DotnetValue dotnet_value(Backoff b) {
    return {"Shop.Backoff", std::to_string(static_cast<int>(b)),
            b == Backoff::Constant ? "Constant" : "Exponential"};
}
}  // namespace shop

using TimeSpan = std::chrono::duration<std::int64_t, std::ratio<1, 10000000>>;

static int failures = 0;
#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
            ++failures;                                                   \
        }                                                                 \
    } while (0)

struct Recorder final : ccm::logging::ILogger {
    ccm::logging::LogState last;
    std::string message;
    bool is_enabled(ccm::logging::LogLevel) const override { return true; }
    void log(ccm::logging::LogLevel, const ccm::logging::EventId&, const std::string& m,
             const std::exception*) override {
        message = m;
    }
    void log_state(ccm::logging::LogLevel level, const ccm::logging::EventId& event,
                   const ccm::logging::LogState& state, const std::exception* e) override {
        last = state;
        log(level, event, state.message, e);
    }
};

// A logger that only knows log(message), as the stash fixture's fakes do.
struct Plain final : ccm::logging::ILogger {
    std::string message;
    bool is_enabled(ccm::logging::LogLevel) const override { return true; }
    void log(ccm::logging::LogLevel, const ccm::logging::EventId&, const std::string& m,
             const std::exception*) override {
        message = m;
    }
};

static std::string part(int i) { return ccm_error_part(i); }

int main() {
    // Values.
    CHECK(ccm::dotnet_value(5).type == "System.Int32");
    CHECK(ccm::dotnet_value(std::int64_t{5}).type == "System.Int64");
    CHECK(ccm::dotnet_value(std::uint8_t{5}).type == "System.Byte");
    CHECK(ccm::dotnet_value(std::size_t{5}).type == "System.UInt64");
    CHECK(ccm::dotnet_value(true).data == "True");
    CHECK(ccm::dotnet_value("x").type == "System.String");
    CHECK(ccm::dotnet_value(static_cast<const char*>(nullptr)).is_null());
    CHECK(ccm::dotnet_value(std::optional<int>()).is_null());
    CHECK(ccm::dotnet_value(std::optional<int>(3)).data == "3");
    CHECK(ccm::dotnet_value(1e20).data == "1E+20");
    CHECK(ccm::dotnet_value(1e16).data == "10000000000000000");
    CHECK(ccm::dotnet_value(1e9f).data == "1E+09");
    CHECK(ccm::dotnet_value(123456790.0f).data == "123456790");
    CHECK(ccm::dotnet_value(0.1f).data == "0.1");
    auto span = ccm::dotnet_value(std::chrono::milliseconds(-1500));
    CHECK(span.type == "System.TimeSpan" && span.data == "-15000000" &&
          span.text == "-00:00:01.5000000");
    CHECK(ccm::time_span_text(TimeSpan(36 * 3600 * 10000000LL).count()) == "1.12:00:00");
    CHECK(ccm::time_span_text(1) == "00:00:00.0000001");

    // An ArgumentOutOfRangeException's actual value, through the error record.
    ccm_status status = ccm::bridge::guard([] {
        throw ccm::ArgumentOutOfRangeException("Delay", TimeSpan(-10000000), "Delay must not be negative.");
    });
    CHECK(status == CCM_ERROR);
    CHECK(part(0) == "System.ArgumentOutOfRangeException" && part(2) == "Delay");
    CHECK(part(4) == "System.TimeSpan" && part(5) == "-10000000");
    status = ccm::bridge::guard([] { throw ccm::ArgumentOutOfRangeException("n", "plain"); });
    CHECK(part(4).empty() && part(5).empty());
    void* origin = reinterpret_cast<void*>(1);
    CHECK(!ccm_error_token(&origin) && origin == nullptr);

    // A cancellation's token: a C# one's origin, and one native code made.
    int released = 0;
    int csharp_token = 42;
    void* native = ccm_token_new(&csharp_token, [](void*) {});
    ccm::tasks::CancellationToken token = ccm::bridge::token(native);
    ccm_token_cancel(native);
    status = ccm::bridge::guard([&] { token.throw_if_cancellation_requested(); });
    CHECK(status == CCM_ERROR && part(0) == "System.OperationCanceledException");
    CHECK(ccm_error_token(&origin) && origin == &csharp_token);
    ccm::tasks::Task<void> delayed = ccm::tasks::delay(std::chrono::milliseconds(-1), token);
    status = ccm::bridge::guard([&] { delayed.get(); });
    CHECK(part(0) == "System.Threading.Tasks.TaskCanceledException");
    CHECK(ccm_error_token(&origin) && origin == &csharp_token);
    {
        ccm::tasks::CancellationTokenSource made;
        ccm::tasks::Task<void> waiting = ccm::tasks::delay(std::chrono::milliseconds(-1), made.token());
        made.cancel();
        try {
            waiting.get();
            CHECK(false);
        } catch (const ccm::TaskCanceledException& e) {
            CHECK(e.cancellation_token() == made.token());
        }
        CHECK(waiting.is_canceled());
        status = ccm::bridge::guard([&] { waiting.get(); });
        CHECK(ccm_error_token(&origin) && origin == nullptr);
    }
    ccm_token_release(native);
    (void)released;
    status = ccm::bridge::guard([] { throw ccm::OperationCanceledException(); });
    CHECK(part(1) == "The operation was canceled." && !ccm_error_token(&origin));

    // A log entry's state.
    Recorder recorder;
    ccm::logging::log_warning(recorder, "Attempt {Attempt} of {Max,3}; {Backoff} in {Delay} {Extra}",
                              2, std::int64_t{3}, shop::Backoff::Exponential,
                              std::chrono::milliseconds(1500), std::optional<int>());
    CHECK(recorder.message == "Attempt 2 of   3; Exponential in 00:00:01.5000000 (null)");
    CHECK(recorder.last.message_template == "Attempt {Attempt} of {Max,3}; {Backoff} in {Delay} {Extra}");
    CHECK(recorder.last.values.size() == 5);
    CHECK(recorder.last.values[0].first == "Attempt" && recorder.last.values[0].second.data == "2");
    CHECK(recorder.last.values[1].first == "Max" && recorder.last.values[1].second.type == "System.Int64");
    CHECK(recorder.last.values[2].second.type == "Shop.Backoff" && recorder.last.values[2].second.data == "2");
    CHECK(recorder.last.values[3].second.type == "System.TimeSpan");
    CHECK(recorder.last.values[4].second.is_null());
    ccm::logging::log_information(recorder, "No {Holes}");
    CHECK(recorder.message == "No {Holes}" && recorder.last.values.empty());
    ccm::logging::log_information(recorder, "Extra {A}", 1, 2);
    CHECK(recorder.message == "Extra 1" && recorder.last.values.size() == 1);
    Plain plain;
    ccm::logging::log_error(plain, ccm::InvalidOperationException("x"), "Stored {Key}", "k");
    CHECK(plain.message == "Stored k");

    // A logged exception's record: a native one's parts.
    status = ccm::bridge::record_exception(ccm::ArgumentOutOfRangeException("Attempts", 0, "too few"));
    CHECK(status == CCM_ERROR && part(4) == "System.Int32" && part(5) == "0");

    std::printf(failures == 0 ? "ALL PASSED\n" : "%d FAILED\n", failures);
    return failures == 0 ? 0 : 1;
}
