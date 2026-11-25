// codecoremorph_tasks.cpp: the bridge's side of .NET's tasks (D-96). Written by CodeCoreMorph;
// do not edit. The retirement deletes it with the bridge.
#include "codecoremorph_tasks.h"

#include <atomic>
#include <memory>
#include <string>
#include <vector>

namespace {

using State = ccm::tasks::detail::CancellationState;
using StatePtr = std::shared_ptr<State>;

std::atomic<ccm_done_fn> done_fn{nullptr};
std::atomic<ccm_done_string_fn> done_string_fn{nullptr};
std::atomic<ccm_done_strings_fn> done_strings_fn{nullptr};
std::atomic<ccm_done_int64_fn> done_int64_fn{nullptr};
std::atomic<ccm_done_double_fn> done_double_fn{nullptr};

StatePtr *token_pointer(const void *token) {
    return static_cast<StatePtr *>(const_cast<void *>(token));  // NOLINT: the bridge's handles
}

template <class T, class V>
void finish(void *source, ccm_status status, V &&value) {
    std::unique_ptr<ccm::tasks::TaskCompletionSource<T>> owned(
        static_cast<ccm::tasks::TaskCompletionSource<T> *>(source));
    if (status == CCM_OK) {
        if constexpr (std::is_void_v<T>) {
            owned->try_set_result();
        } else {
            owned->try_set_result(std::forward<V>(value));
        }
    } else if (status == CCM_CANCELED) {
        owned->try_set_canceled();
    } else {
        owned->try_set_exception(ccm::bridge::take_managed());
    }
}

}  // namespace

// A C# cancellation native code sees (a C# task canceled, a callback that threw one) is caught
// as one, and cancels the native task it ends, as in C#; the façade throws the C# object again.
CCM_REGISTER_EXCEPTION("System.OperationCanceledException", ccm::OperationCanceledException);
CCM_REGISTER_EXCEPTION("System.Threading.Tasks.TaskCanceledException", ccm::TaskCanceledException);

namespace ccm::bridge {

ccm::tasks::CancellationToken token(void *cancellation) {
    if (cancellation == nullptr) return {};
    return ccm::tasks::CancellationToken(*token_pointer(cancellation));
}

TokenOut::TokenOut(const ccm::tasks::CancellationToken &token) {
    if (token.can_be_canceled()) pointer_ = new StatePtr(token.state());  // NOLINT: freed below
}

TokenOut::~TokenOut() { delete token_pointer(pointer_); }  // NOLINT

namespace detail {

void done(void *task, ccm_status status) {
    if (auto f = done_fn.load()) f(task, status);
}

void done_string(void *task, ccm_status status, const char *result) {
    if (auto f = done_string_fn.load()) f(task, status, result);
}

void done_strings(void *task, ccm_status status, const std::vector<std::string> *result) {
    auto f = done_strings_fn.load();
    if (f == nullptr) return;
    if (result == nullptr) {
        f(task, status, nullptr, 0);
        return;
    }
    std::vector<const char *> pointers;
    pointers.reserve(result->size());
    for (const auto &s : *result) pointers.push_back(s.c_str());
    f(task, status, pointers.data(), static_cast<int32_t>(pointers.size()));
}

void done_int64(void *task, ccm_status status, int64_t result) {
    if (auto f = done_int64_fn.load()) f(task, status, result);
}

void done_double(void *task, ccm_status status, double result) {
    if (auto f = done_double_fn.load()) f(task, status, result);
}

}  // namespace detail
}  // namespace ccm::bridge

extern "C" {

CCM_EXPORT void ccm_tasks_init(ccm_done_fn done, ccm_done_string_fn done_string,
                               ccm_done_strings_fn done_strings, ccm_done_int64_fn done_int64,
                               ccm_done_double_fn done_double, ccm_done_handle_fn /*done_handle*/) {
    done_fn.store(done);
    done_string_fn.store(done_string);
    done_strings_fn.store(done_strings);
    done_int64_fn.store(done_int64);
    done_double_fn.store(done_double);
}

CCM_EXPORT void *ccm_token_new(void *origin, ccm_release_fn release) {
    try {
        auto state = std::make_shared<State>();
        state->origin = ccm::bridge::ManagedRef(origin, release).shared();
        return new StatePtr(std::move(state));  // NOLINT: the façade releases it
    } catch (...) {
        return nullptr;
    }
}

CCM_EXPORT void ccm_token_cancel(void *token) {
    if (token != nullptr) (*token_pointer(token))->cancel();
}

CCM_EXPORT void ccm_token_release(void *token) { delete token_pointer(token); }  // NOLINT

CCM_EXPORT void *ccm_token_origin(const void *token) {
    if (token == nullptr) return nullptr;
    return (*token_pointer(token))->origin.get();
}

CCM_EXPORT void ccm_token_on_cancel(const void *token, ccm_cancel_fn cancel, void *context,
                                    ccm_release_fn release) {
    ccm::bridge::ManagedRef managed(context, release);
    if (token == nullptr || cancel == nullptr) return;
    try {
        (*token_pointer(token))->add([cancel, managed] { cancel(managed.get()); });
    } catch (...) {
    }
}

CCM_EXPORT void ccm_source_done(void *source, ccm_status status) {
    finish<void>(source, status, 0);
}

CCM_EXPORT void ccm_source_done_string(void *source, ccm_status status, const char *result) {
    finish<std::optional<std::string>>(
        source, status, result != nullptr ? std::optional<std::string>(result) : std::nullopt);
}

CCM_EXPORT void ccm_source_done_int64(void *source, ccm_status status, int64_t result) {
    finish<int64_t>(source, status, result);
}

CCM_EXPORT void ccm_source_done_double(void *source, ccm_status status, double result) {
    finish<double>(source, status, result);
}

}  // extern "C"
