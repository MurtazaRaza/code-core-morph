/* codecoremorph_tasks.h: the bridge's side of .NET's tasks (D-96). Written by CodeCoreMorph; do
 * not edit. The retirement deletes it with the bridge.
 *
 * A façade's async member returns the C# task its export completes: the export takes the
 * task's context (void *task, the façades' support's) and starts the native task, which,
 * when done, calls the support's completion for its kind of result, once, from the thread that
 * finished it: CCM_OK and the result; CCM_ERROR, the error's parts readable during the call
 * (ccm_error_part; a canceled task's is its OperationCanceledException, with its token,
 * ccm_error_token); or
 * CCM_MANAGED, the C# exception's handle readable during the call (ccm_error_managed). A
 * cancellation token crosses as a void * (null for CancellationToken.None), borrowed for the
 * call. A C# task native code awaits completes the native source it was given, once. */
#ifndef CODECOREMORPH_TASKS_H
#define CODECOREMORPH_TASKS_H

#include "codecoremorph_bridge.h"

#ifdef __cplusplus
extern "C" {
#endif

/* A C# task canceled: the status a C# task's source is completed with. */
#define CCM_CANCELED 3

typedef void (*ccm_done_fn)(void *task, ccm_status status);
typedef void (*ccm_done_string_fn)(void *task, ccm_status status, const char *result);
typedef void (*ccm_done_strings_fn)(void *task, ccm_status status, const char *const *result,
                                    int32_t count);
typedef void (*ccm_done_int64_fn)(void *task, ccm_status status, int64_t result);
typedef void (*ccm_done_double_fn)(void *task, ccm_status status, double result);
typedef void (*ccm_done_handle_fn)(void *task, ccm_status status, void *result);
typedef void (*ccm_cancel_fn)(void *context);

/* The support's completions, registered once before the first task. */
CCM_EXPORT void ccm_tasks_init(ccm_done_fn done, ccm_done_string_fn done_string,
                               ccm_done_strings_fn done_strings, ccm_done_int64_fn done_int64,
                               ccm_done_double_fn done_double, ccm_done_handle_fn done_handle);

/* A native token for a C# one (origin: a GCHandle of the boxed C# token, released with the
 * token's state), which the façade cancels when the C# token is and releases when its task is
 * done. */
CCM_EXPORT void *ccm_token_new(void *origin, ccm_release_fn release);
CCM_EXPORT void ccm_token_cancel(void *token);
CCM_EXPORT void ccm_token_release(void *token);
/* The C# token a native one stands for (a GCHandle the token keeps), or null for one native
 * code made. */
CCM_EXPORT void *ccm_token_origin(const void *token);
/* For a native token native code made: cancel(context) when it is canceled, at once if it is
 * already; release(context) when its state goes. */
CCM_EXPORT void ccm_token_on_cancel(const void *token, ccm_cancel_fn cancel, void *context,
                                    ccm_release_fn release);

/* A C# task's outcome, for the native source the call into C# was given; status CCM_OK,
 * CCM_MANAGED (the C# exception recorded first with ccm_set_managed: a canceled task's is the
 * exception awaiting it throws, which cancels the native task, D-97) or CCM_CANCELED. */
CCM_EXPORT void ccm_source_done(void *source, ccm_status status);
CCM_EXPORT void ccm_source_done_string(void *source, ccm_status status, const char *result);
CCM_EXPORT void ccm_source_done_int64(void *source, ccm_status status, int64_t result);
CCM_EXPORT void ccm_source_done_double(void *source, ccm_status status, double result);

#ifdef __cplusplus
}

#include <chrono>
#include <cstdint>
#include <exception>
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "codecoremorph_bridge.hpp"
#include "codecoremorph_tasks.hpp"

namespace ccm::bridge {

/* The native token a C# one crossed as (CancellationToken.None for null). */
ccm::tasks::CancellationToken token(void *cancellation);

/* A native token as a call into C# passes it: a void * valid for the call. */
class TokenOut {
public:
    explicit TokenOut(const ccm::tasks::CancellationToken &token);
    TokenOut(const TokenOut &) = delete;
    TokenOut &operator=(const TokenOut &) = delete;
    ~TokenOut();
    void *get() const { return pointer_; }

private:
    void *pointer_ = nullptr;
};

/* TimeSpan's ticks (100 ns) as the milliseconds Task.Delay waits: whole ones, as .NET's. */
inline std::chrono::milliseconds milliseconds(int64_t ticks) {
    return std::chrono::milliseconds(ticks / 10000);
}

namespace detail {
void done(void *task, ccm_status status);
void done_string(void *task, ccm_status status, const char *result);
void done_strings(void *task, ccm_status status, const std::vector<std::string> *result);
void done_int64(void *task, ccm_status status, int64_t result);
void done_double(void *task, ccm_status status, double result);

/* The status of a failed task's exception, its parts recorded for the completion. */
inline ccm_status status_of(const std::exception_ptr &error) {
    try {
        std::rethrow_exception(error);
    } catch (...) {
        return current_exception_status();
    }
}

template <class T>
void complete(void *context, const ccm::tasks::Task<T> &task) {
    if (std::exception_ptr error = task.exception()) {
        ccm_status status = status_of(error);
        if constexpr (std::is_void_v<T>) {
            done(context, status);
        } else if constexpr (std::is_same_v<T, std::vector<std::string>>) {
            done_strings(context, status, nullptr);
        } else if constexpr (std::is_floating_point_v<T>) {
            done_double(context, status, 0);
        } else if constexpr (std::is_integral_v<T>) {
            done_int64(context, status, 0);
        } else {
            done_string(context, status, nullptr);
        }
        return;
    }
    if constexpr (std::is_void_v<T>) {
        done(context, CCM_OK);
    } else if constexpr (std::is_same_v<T, std::vector<std::string>>) {
        std::vector<std::string> values = task.result();
        done_strings(context, CCM_OK, &values);
    } else if constexpr (std::is_same_v<T, std::optional<std::string>>) {
        std::optional<std::string> value = task.result();
        done_string(context, CCM_OK, value ? value->c_str() : nullptr);
    } else if constexpr (std::is_same_v<T, std::string>) {
        std::string value = task.result();
        done_string(context, CCM_OK, value.c_str());
    } else if constexpr (std::is_floating_point_v<T>) {
        done_double(context, CCM_OK, static_cast<double>(task.result()));
    } else {
        done_int64(context, CCM_OK, static_cast<int64_t>(task.result()));
    }
}
}  // namespace detail

/* An async export's body: starts the native task body returns and completes the façade's C#
 * task (context) with its outcome. What body throws faults the task, as an async C# method's
 * argument checks do; a task done at once completes the C# one before the export returns. */
template <class F>
void start(void *context, F &&body) {
    using TaskType = std::invoke_result_t<F>;
    TaskType task;
    try {
        task = std::forward<F>(body)();
    } catch (...) {
        task = TaskType::from_exception(std::current_exception());
    }
    task.on_done([task, context] { detail::complete(context, task); });
}

/* A C# task native code awaits: call makes the call into C#, given the native source the
 * support completes, and returns its status; a C# exception the call threw is thrown here, by
 * its type, as C# would throw it at the call. */
template <class T, class Call>
ccm::tasks::Task<T> managed_task(Call &&call) {
    auto source = std::make_unique<ccm::tasks::TaskCompletionSource<T>>();
    ccm::tasks::Task<T> task = source->task();
    ccm_status status = std::forward<Call>(call)(static_cast<void *>(source.get()));
    check_managed(status);
    source.release();  // NOLINT(bugprone-unused-return-value): the support's until its done call
    return task;
}

}  // namespace ccm::bridge
#endif

#endif
