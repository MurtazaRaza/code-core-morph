// codecoremorph_tasks.hpp: .NET's tasks for a C++ port of C# code (CodeCoreMorph, D-96).
// Written by CodeCoreMorph; do not edit. C++20.
//
// An async C# method is a coroutine returning ccm::tasks::Task<T>. As in C#, it runs at once,
// on the caller's thread, until its first co_await of something not done yet, and its
// exceptions, argument checks too, go into the task rather than out of the call. co_await on
// a task gives its result or throws its exception; the coroutine resumes on the thread that
// completed what it awaited, as C# code after ConfigureAwait(false) does. TaskCompletionSource
// makes a task by hand (the bridge's, for a C# task); when_all is Task.WhenAll; delay is
// Task.Delay; CancellationTokenSource and CancellationToken are .NET's, and an
// OperationCanceledException carries the token that canceled it, as .NET's does (D-97). get()
// waits for a task on a thread that isn't one of its own (a test's).
#ifndef CODECOREMORPH_TASKS_HPP
#define CODECOREMORPH_TASKS_HPP

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <coroutine>
#include <cstdint>
#include <exception>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

#include "codecoremorph_exceptions.hpp"

namespace ccm {
namespace tasks {

// ------------------------------------------------------------------------------- cancellation

namespace detail {

class CancellationState {
public:
    using Callback = std::function<void()>;

    bool canceled() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return canceled_;
    }

    // Runs callback at cancellation, or at once if canceled; 0 when it ran at once.
    std::uint64_t add(Callback callback) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!canceled_) {
                std::uint64_t id = ++next_;
                callbacks_.emplace_back(id, std::move(callback));
                return id;
            }
        }
        callback();
        return 0;
    }

    void remove(std::uint64_t id) {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto it = callbacks_.begin(); it != callbacks_.end(); ++it) {
            if (it->first == id) {
                callbacks_.erase(it);
                return;
            }
        }
    }

    void cancel() {
        std::vector<std::pair<std::uint64_t, Callback>> run;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (canceled_) return;
            canceled_ = true;
            run.swap(callbacks_);
        }
        changed_.notify_all();
        for (auto& [id, callback] : run) callback();
    }

    // Blocks until canceled or duration is up (a negative one: until canceled); whether it
    // was canceled.
    bool wait_for(std::chrono::milliseconds duration) const {
        std::unique_lock<std::mutex> lock(mutex_);
        if (duration.count() < 0) {
            changed_.wait(lock, [this] { return canceled_; });
            return true;
        }
        return changed_.wait_for(lock, duration, [this] { return canceled_; });
    }

    // What the bridge keeps with a token: the C# token it stands for.
    std::shared_ptr<void> origin;

private:
    mutable std::mutex mutex_;
    mutable std::condition_variable changed_;
    bool canceled_ = false;
    std::uint64_t next_ = 0;
    std::vector<std::pair<std::uint64_t, Callback>> callbacks_;
};

}  // namespace detail

// A registered callback, unregistered when this goes (CancellationTokenRegistration).
class CancellationRegistration {
public:
    CancellationRegistration() = default;
    CancellationRegistration(std::shared_ptr<detail::CancellationState> state, std::uint64_t id)
        : state_(std::move(state)), id_(id) {}
    CancellationRegistration(const CancellationRegistration&) = delete;
    CancellationRegistration& operator=(const CancellationRegistration&) = delete;
    CancellationRegistration(CancellationRegistration&& other) noexcept
        : state_(std::move(other.state_)), id_(std::exchange(other.id_, 0)) {}
    CancellationRegistration& operator=(CancellationRegistration&& other) noexcept {
        if (this != &other) {
            reset();
            state_ = std::move(other.state_);
            id_ = std::exchange(other.id_, 0);
        }
        return *this;
    }
    ~CancellationRegistration() { reset(); }

    void reset() {
        if (state_ && id_ != 0) state_->remove(id_);
        state_.reset();
        id_ = 0;
    }

private:
    std::shared_ptr<detail::CancellationState> state_;
    std::uint64_t id_ = 0;
};

// .NET's CancellationToken: default, it can't be canceled (CancellationToken.None).
class CancellationToken {
public:
    CancellationToken() = default;
    explicit CancellationToken(std::shared_ptr<detail::CancellationState> state)
        : state_(std::move(state)) {}

    bool can_be_canceled() const { return state_ != nullptr; }
    bool is_cancellation_requested() const { return state_ && state_->canceled(); }

    // ThrowIfCancellationRequested: an OperationCanceledException with this token.
    void throw_if_cancellation_requested() const;

    // As .NET's: two tokens are equal when they come from one source (or neither can be
    // canceled).
    friend bool operator==(const CancellationToken& a, const CancellationToken& b) {
        return a.state_ == b.state_;
    }

    // Register: callback runs when the token is canceled, at once if it is already.
    CancellationRegistration register_callback(std::function<void()> callback) const {
        if (!state_) return {};
        std::uint64_t id = state_->add(std::move(callback));
        return id == 0 ? CancellationRegistration() : CancellationRegistration(state_, id);
    }

    // WaitHandle.WaitOne(timeout): blocks until the token is canceled or the time is up (a
    // negative time, Timeout.Infinite, until it is canceled); whether it was canceled. A token
    // that can't be canceled sleeps the time out.
    bool wait_for(std::chrono::milliseconds duration) const {
        if (state_) return state_->wait_for(duration);
        if (duration.count() >= 0) {
            std::this_thread::sleep_for(duration);
            return false;
        }
        detail::CancellationState never;
        return never.wait_for(duration);
    }

    const std::shared_ptr<detail::CancellationState>& state() const { return state_; }

private:
    std::shared_ptr<detail::CancellationState> state_;
};

class CancellationTokenSource {
public:
    CancellationTokenSource() : state_(std::make_shared<detail::CancellationState>()) {}
    CancellationToken token() const { return CancellationToken(state_); }
    void cancel() { state_->cancel(); }
    bool is_cancellation_requested() const { return state_->canceled(); }

private:
    std::shared_ptr<detail::CancellationState> state_;
};

}  // namespace tasks

// .NET's OperationCanceledException: its token (CancellationToken), which a façade's exception
// carries as the C# token it stands for (D-97).
class OperationCanceledException : public Exception {
public:
    explicit OperationCanceledException(const std::string& message = "The operation was canceled.",
                                        tasks::CancellationToken token = {})
        : Exception(message), token_(std::move(token)) {}
    const char* dotnet_type() const noexcept override { return "System.OperationCanceledException"; }
    const tasks::CancellationToken& cancellation_token() const noexcept { return token_; }
    std::optional<std::shared_ptr<void>> token_origin() const override {
        if (!token_.can_be_canceled()) return std::nullopt;
        return token_.state()->origin;
    }

private:
    tasks::CancellationToken token_;
};

// What awaiting a canceled task throws, as in .NET: Task.Delay's carries its token.
class TaskCanceledException : public OperationCanceledException {
public:
    explicit TaskCanceledException(const std::string& message = "A task was canceled.",
                                   tasks::CancellationToken token = {})
        : OperationCanceledException(message, std::move(token)) {}
    const char* dotnet_type() const noexcept override {
        return "System.Threading.Tasks.TaskCanceledException";
    }
};

namespace tasks {

inline void CancellationToken::throw_if_cancellation_requested() const {
    if (is_cancellation_requested()) {
        throw OperationCanceledException("The operation was canceled.", *this);
    }
}

// ------------------------------------------------------------------------------- tasks

template <class T>
class Task;

namespace detail {

struct Unit {};

template <class T>
using Value = std::conditional_t<std::is_void_v<T>, Unit, T>;

// A task's outcome, shared by the coroutine, its awaiters and its completion source.
template <class T>
class State {
public:
    bool done() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return done_;
    }

    bool faulted() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return done_ && error_ != nullptr && !canceled_;
    }

    bool canceled() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return done_ && canceled_;
    }

    bool set_value(Value<T> value) {
        return finish([&] { value_.emplace(std::move(value)); });
    }

    // An OperationCanceledException (TaskCanceledException among them) cancels the task, as
    // an async C# method's does.
    bool set_exception(std::exception_ptr error) {
        bool canceled = false;
        try {
            std::rethrow_exception(error);
        } catch (const OperationCanceledException&) {
            canceled = true;
        } catch (...) {
        }
        return finish([&] {
            error_ = std::move(error);
            canceled_ = canceled;
        });
    }

    bool set_canceled(CancellationToken token = {}) {
        return set_exception(
            std::make_exception_ptr(TaskCanceledException("A task was canceled.", std::move(token))));
    }

    // Runs f when done; false (f not kept) when done already.
    bool when_done(std::function<void()> f) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!done_) {
                continuations_.push_back(std::move(f));
                return true;
            }
        }
        return false;
    }

    // The result, or the exception thrown (a canceled task's own, as .NET rethrows it).
    Value<T> take() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (error_) std::rethrow_exception(error_);
        if constexpr (std::is_copy_constructible_v<Value<T>>) {
            return *value_;
        } else {
            return std::move(*value_);
        }
    }

    std::exception_ptr error() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return error_;
    }

    void wait() {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [this] { return done_; });
    }

private:
    template <class F>
    bool finish(F set) {
        std::vector<std::function<void()>> run;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (done_) return false;
            set();
            done_ = true;
            run.swap(continuations_);
        }
        changed_.notify_all();
        for (auto& f : run) f();
        return true;
    }

    mutable std::mutex mutex_;
    std::condition_variable changed_;
    bool done_ = false;
    bool canceled_ = false;
    std::optional<Value<T>> value_;
    std::exception_ptr error_;
    std::vector<std::function<void()>> continuations_;
};

// A coroutine's promise: the outcome is kept at co_return (or a throw) and published at its
// final suspension, once its locals are gone, as C# sets an async method's result after its
// finally blocks (a lock's release) have run: what awaits it resumes then, on this thread.
template <class T>
struct PromiseBase {
    std::shared_ptr<State<T>> state = std::make_shared<State<T>>();
    std::optional<Value<T>> value;
    std::exception_ptr error;

    std::suspend_never initial_suspend() noexcept { return {}; }  // runs at once, as C#'s

    template <class Promise>
    struct Final {
        bool await_ready() noexcept { return false; }
        void await_suspend(std::coroutine_handle<Promise> frame) noexcept {
            std::shared_ptr<State<T>> outcome = frame.promise().state;
            std::optional<Value<T>> result = std::move(frame.promise().value);
            std::exception_ptr failure = frame.promise().error;
            frame.destroy();  // before what awaits resumes, on this thread's stack
            if (failure) {
                outcome->set_exception(std::move(failure));
            } else {
                outcome->set_value(std::move(*result));
            }
        }
        void await_resume() noexcept {}
    };

    void unhandled_exception() noexcept { error = std::current_exception(); }
};

}  // namespace detail

// .NET's Task<T> (Task for void): a coroutine's result, awaited with co_await.
template <class T = void>
class Task {
public:
    struct promise_type : detail::PromiseBase<T> {
        Task get_return_object() { return Task(this->state); }
        auto final_suspend() noexcept { return typename detail::PromiseBase<T>::template Final<promise_type>{}; }
        template <class V>
        void return_value(V&& value) {
            this->value.emplace(std::forward<V>(value));
        }
    };

    Task() = default;
    explicit Task(std::shared_ptr<detail::State<T>> state) : state_(std::move(state)) {}

    bool valid() const { return state_ != nullptr; }
    bool is_completed() const { return state_->done(); }
    bool is_faulted() const { return state_->faulted(); }
    bool is_canceled() const { return state_->canceled(); }

    // Waits for the task, then gives its result or throws its exception (Task.Result, unwrapped).
    T get() const {
        state_->wait();
        return state_->take();
    }

    // Runs f once the task is done, on the thread that completes it (at once if done).
    void on_done(std::function<void()> f) const {
        if (!state_->when_done(f)) f();
    }

    // The outcome, once done: the result, or the exception thrown.
    T result() const { return state_->take(); }
    std::exception_ptr exception() const { return state_->error(); }

    bool await_ready() const noexcept { return state_->done(); }
    bool await_suspend(std::coroutine_handle<> awaiting) const {
        return state_->when_done([awaiting] { awaiting.resume(); });
    }
    T await_resume() const { return state_->take(); }

    static Task from_result(T value) {
        auto state = std::make_shared<detail::State<T>>();
        state->set_value(std::move(value));
        return Task(state);
    }

    static Task from_exception(std::exception_ptr error) {
        auto state = std::make_shared<detail::State<T>>();
        state->set_exception(std::move(error));
        return Task(state);
    }

private:
    std::shared_ptr<detail::State<T>> state_;
};

template <>
class Task<void> {
public:
    struct promise_type : detail::PromiseBase<void> {
        Task get_return_object() { return Task(this->state); }
        auto final_suspend() noexcept { return detail::PromiseBase<void>::Final<promise_type>{}; }
        void return_void() { this->value.emplace(detail::Unit{}); }
    };

    Task() = default;
    explicit Task(std::shared_ptr<detail::State<void>> state) : state_(std::move(state)) {}

    bool valid() const { return state_ != nullptr; }
    bool is_completed() const { return state_->done(); }
    bool is_faulted() const { return state_->faulted(); }
    bool is_canceled() const { return state_->canceled(); }
    void get() const {
        state_->wait();
        state_->take();
    }
    void on_done(std::function<void()> f) const {
        if (!state_->when_done(f)) f();
    }
    void result() const { state_->take(); }
    std::exception_ptr exception() const { return state_->error(); }

    bool await_ready() const noexcept { return state_->done(); }
    bool await_suspend(std::coroutine_handle<> awaiting) const {
        return state_->when_done([awaiting] { awaiting.resume(); });
    }
    void await_resume() const { state_->take(); }

    static Task completed() {
        auto state = std::make_shared<detail::State<void>>();
        state->set_value(detail::Unit{});
        return Task(state);
    }

    static Task from_exception(std::exception_ptr error) {
        auto state = std::make_shared<detail::State<void>>();
        state->set_exception(std::move(error));
        return Task(state);
    }

private:
    std::shared_ptr<detail::State<void>> state_;
};

// .NET's TaskCompletionSource<T>: a task completed by hand, once.
template <class T = void>
class TaskCompletionSource {
public:
    TaskCompletionSource() : state_(std::make_shared<detail::State<T>>()) {}
    Task<T> task() const { return Task<T>(state_); }

    template <class V = detail::Value<T>>
    bool try_set_result(V value = V()) {
        return state_->set_value(std::move(value));
    }
    bool try_set_exception(std::exception_ptr error) { return state_->set_exception(std::move(error)); }
    // TrySetCanceled(token): awaiting the task throws a TaskCanceledException with the token.
    bool try_set_canceled(CancellationToken token = {}) { return state_->set_canceled(std::move(token)); }

private:
    std::shared_ptr<detail::State<T>> state_;
};

// Task.WhenAll: each task's result, in their order, once every one is done; if any failed,
// the first that did, in their order, as awaiting Task.WhenAll throws.
template <class T>
Task<std::vector<T>> when_all(std::vector<Task<T>> tasks) {
    std::vector<T> results;
    results.reserve(tasks.size());
    std::exception_ptr first;
    for (auto& task : tasks) {
        try {
            results.push_back(co_await task);
        } catch (...) {
            if (!first) first = std::current_exception();
        }
    }
    if (first) std::rethrow_exception(first);
    co_return results;
}

inline Task<void> when_all(std::vector<Task<void>> tasks) {
    std::exception_ptr first;
    for (auto& task : tasks) {
        try {
            co_await task;
        } catch (...) {
            if (!first) first = std::current_exception();
        }
    }
    if (first) std::rethrow_exception(first);
}

namespace detail {

// The threads the runtime's own work runs on (a timer's completions): .NET's thread pool's
// part. Never destroyed: a native library can't join threads while its process exits.
class Pool {
public:
    static Pool& instance() {
        static Pool* pool = new Pool();  // NOLINT(cppcoreguidelines-owning-memory): kept for good
        return *pool;
    }

    void post(std::function<void()> work) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            queue_.push_back(std::move(work));
        }
        ready_.notify_one();
    }

private:
    Pool() {
        unsigned count = std::max(2U, std::thread::hardware_concurrency());
        for (unsigned i = 0; i < count; ++i) std::thread([this] { run(); }).detach();
    }

    void run() {
        for (;;) {
            std::function<void()> work;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                ready_.wait(lock, [this] { return !queue_.empty(); });
                work = std::move(queue_.front());
                queue_.erase(queue_.begin());
            }
            work();
        }
    }

    std::mutex mutex_;
    std::condition_variable ready_;
    std::vector<std::function<void()>> queue_;
};

// One thread for every delay's deadline, which hands each due one to the pool.
class Timer {
public:
    static Timer& instance() {
        static Timer* timer = new Timer();  // NOLINT(cppcoreguidelines-owning-memory): kept for good
        return *timer;
    }

    void at(std::chrono::steady_clock::time_point when, std::function<void()> work) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            due_.emplace(when, std::move(work));
        }
        changed_.notify_one();
    }

private:
    Timer() {
        std::thread([this] { run(); }).detach();
    }

    void run() {
        std::unique_lock<std::mutex> lock(mutex_);
        for (;;) {
            if (due_.empty()) {
                changed_.wait(lock);
                continue;
            }
            auto next = due_.begin();
            if (std::chrono::steady_clock::now() < next->first) {
                changed_.wait_until(lock, next->first);
                continue;
            }
            std::function<void()> work = std::move(next->second);
            due_.erase(next);
            Pool::instance().post(std::move(work));
        }
    }

    std::mutex mutex_;
    std::condition_variable changed_;
    std::multimap<std::chrono::steady_clock::time_point, std::function<void()>> due_;
};

}  // namespace detail

// Task.Delay: done after duration (a negative one, Timeout.Infinite, never), canceled with
// the token, a TaskCanceledException carrying it, as .NET's.
inline Task<void> delay(std::chrono::milliseconds duration, CancellationToken cancellation = {}) {
    if (cancellation.is_cancellation_requested()) {
        return Task<void>::from_exception(
            std::make_exception_ptr(TaskCanceledException("A task was canceled.", cancellation)));
    }
    auto source = std::make_shared<TaskCompletionSource<void>>();
    if (duration.count() >= 0) {
        detail::Timer::instance().at(std::chrono::steady_clock::now() + duration,
                                     [source] { source->try_set_result(); });
    }
    // The callback holds the token's state weakly, as the state holds the callback.
    std::weak_ptr<detail::CancellationState> weak = cancellation.state();
    auto registration = std::make_shared<CancellationRegistration>(
        cancellation.register_callback([source, weak] {
            source->try_set_canceled(CancellationToken(weak.lock()));
        }));
    Task<void> task = source->task();
    task.on_done([registration] { registration->reset(); });
    return task;
}

}  // namespace tasks
}  // namespace ccm

#endif
