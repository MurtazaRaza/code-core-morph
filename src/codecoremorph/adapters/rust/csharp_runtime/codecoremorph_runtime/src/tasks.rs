//! .NET's tasks for a Rust port of C# code (CodeCoreMorph, D-96). Written by CodeCoreMorph; do
//! not edit. The retirement keeps this module.
//!
//! An async C# method is a function that returns [`spawn`]'s [`Task`] of an `async` block. As
//! in C#, the block runs at once, on the caller's thread, until its first `.await` of something
//! not done yet, and its errors, argument checks too, go into the task rather than out of the
//! call; after that it runs on the runtime's threads, woken by what it awaits. A task is a
//! `Future` of `Result<T>` that may be awaited many times (its outcome is cloned), or waited
//! for ([`Task::wait`], a test's). [`TaskCompletionSource`] makes a task by hand; [`when_all`]
//! is Task.WhenAll; [`delay`] is Task.Delay; [`CancellationTokenSource`] and
//! [`CancellationToken`] are .NET's. An error that is an OperationCanceledException cancels the
//! task, as in C#, and carries the token that canceled it ([`Exception::cancellation_token`],
//! D-97).
use std::any::Any;
use std::collections::BTreeMap;
use std::future::Future;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, OnceLock};
use std::task::{Context, Poll, Wake, Waker};
use std::thread;
use std::time::{Duration, Instant};

use crate::exceptions::CanceledBy;
use crate::{Exception, Result};

fn lock<T: ?Sized>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

// ------------------------------------------------------------------------------- cancellation

type Callback = Box<dyn FnOnce() + Send>;

#[derive(Default)]
struct CancelState {
    canceled: bool,
    next: u64,
    callbacks: Vec<(u64, Callback)>,
}

/// What a token's state keeps for the bridge: the C# token it stands for.
pub type TokenOrigin = Arc<dyn Any + Send + Sync>;

#[derive(Default)]
struct Cancellation {
    state: Mutex<CancelState>,
    changed: Condvar,
    origin: Mutex<Option<TokenOrigin>>,
}

impl Cancellation {
    fn cancel(&self) {
        let run = {
            let mut state = lock(&self.state);
            if state.canceled {
                return;
            }
            state.canceled = true;
            std::mem::take(&mut state.callbacks)
        };
        self.changed.notify_all();
        for (_, callback) in run {
            callback();
        }
    }

    /// Blocks until canceled or `timeout` is up (None: until canceled); whether it was.
    fn wait(&self, timeout: Option<Duration>) -> bool {
        let state = lock(&self.state);
        match timeout {
            None => {
                self.changed.wait_while(state, |s| !s.canceled).unwrap_or_else(|p| p.into_inner()).canceled
            }
            Some(timeout) => {
                let waited = self.changed.wait_timeout_while(state, timeout, |s| !s.canceled);
                waited.unwrap_or_else(|p| p.into_inner()).0.canceled
            }
        }
    }
}

/// .NET's CancellationToken: [`CancellationToken::none`] can't be canceled.
#[derive(Clone, Default)]
pub struct CancellationToken {
    state: Option<Arc<Cancellation>>,
}

impl CancellationToken {
    /// CancellationToken.None.
    pub fn none() -> CancellationToken {
        CancellationToken { state: None }
    }

    pub fn can_be_canceled(&self) -> bool {
        self.state.is_some()
    }

    pub fn is_cancellation_requested(&self) -> bool {
        self.state.as_ref().is_some_and(|s| lock(&s.state).canceled)
    }

    /// ThrowIfCancellationRequested: an OperationCanceledException with this token.
    pub fn throw_if_cancellation_requested(&self) -> Result<()> {
        if self.is_cancellation_requested() {
            return Err(Exception::operation_canceled_by(self));
        }
        Ok(())
    }

    /// Register: `callback` runs when the token is canceled, at once if it is already; it is
    /// unregistered when the registration drops.
    pub fn register(&self, callback: impl FnOnce() + Send + 'static) -> CancellationRegistration {
        let Some(shared) = &self.state else { return CancellationRegistration::default() };
        let mut state = lock(&shared.state);
        if state.canceled {
            drop(state);
            callback();
            return CancellationRegistration::default();
        }
        state.next += 1;
        let id = state.next;
        state.callbacks.push((id, Box::new(callback)));
        CancellationRegistration { state: Some(Arc::clone(shared)), id }
    }

    /// WaitHandle.WaitOne(timeout): blocks until the token is canceled or `timeout` is up
    /// (None, Timeout.Infinite: until it is canceled); whether it was canceled. A token that
    /// can't be canceled sleeps the time out.
    pub fn wait(&self, timeout: Option<Duration>) -> bool {
        match (&self.state, timeout) {
            (Some(shared), _) => shared.wait(timeout),
            (None, Some(timeout)) => {
                thread::sleep(timeout);
                false
            }
            (None, None) => Cancellation::default().wait(None),
        }
    }

    /// The C# token this one stands for, for the bridge.
    pub fn origin(&self) -> Option<TokenOrigin> {
        self.state.as_ref().and_then(|s| lock(&s.origin).clone())
    }

    /// Whether two tokens share one source.
    pub fn same(&self, other: &CancellationToken) -> bool {
        match (&self.state, &other.state) {
            (Some(a), Some(b)) => Arc::ptr_eq(a, b),
            (None, None) => true,
            _ => false,
        }
    }
}

/// A registered callback, unregistered when this drops (CancellationTokenRegistration).
#[derive(Default)]
pub struct CancellationRegistration {
    state: Option<Arc<Cancellation>>,
    id: u64,
}

impl Drop for CancellationRegistration {
    fn drop(&mut self) {
        if let Some(shared) = &self.state {
            lock(&shared.state).callbacks.retain(|(id, _)| *id != self.id);
        }
    }
}

/// .NET's CancellationTokenSource.
#[derive(Clone, Default)]
pub struct CancellationTokenSource {
    state: Arc<Cancellation>,
}

impl CancellationTokenSource {
    pub fn new() -> CancellationTokenSource {
        CancellationTokenSource::default()
    }

    /// A source whose token stands for a C# one (the bridge's).
    pub fn with_origin(origin: TokenOrigin) -> CancellationTokenSource {
        let source = CancellationTokenSource::default();
        *lock(&source.state.origin) = Some(origin);
        source
    }

    pub fn token(&self) -> CancellationToken {
        CancellationToken { state: Some(Arc::clone(&self.state)) }
    }

    pub fn cancel(&self) {
        self.state.cancel();
    }

    pub fn is_cancellation_requested(&self) -> bool {
        lock(&self.state.state).canceled
    }
}

impl Exception {
    /// `OperationCanceledException(message, token)` with .NET's message: what
    /// ThrowIfCancellationRequested throws.
    pub fn operation_canceled_by(token: &CancellationToken) -> Exception {
        Exception::operation_canceled().with_cancellation_token(token)
    }

    /// A `TaskCanceledException` with the token: what awaiting a task the token canceled gives.
    pub fn task_canceled_by(token: &CancellationToken) -> Exception {
        Exception::task_canceled().with_cancellation_token(token)
    }

    /// The exception with `token` as its CancellationToken (a project's own
    /// OperationCanceledException); a façade's exception carries the C# token it stands for.
    pub fn with_cancellation_token(mut self, token: &CancellationToken) -> Exception {
        self.set_canceled_by(token.can_be_canceled().then(|| CanceledBy {
            token: Arc::new(token.clone()),
            origin: token.origin(),
        }));
        self
    }

    /// An OperationCanceledException's CancellationToken: [`CancellationToken::none`] for one
    /// without.
    pub fn cancellation_token(&self) -> CancellationToken {
        self.canceled_by()
            .and_then(|c| c.token.downcast_ref::<CancellationToken>().cloned())
            .unwrap_or_default()
    }
}

// ------------------------------------------------------------------------------- tasks

type Continuation<T> = Box<dyn FnOnce(&Result<T>) + Send>;

struct TaskState<T> {
    outcome: Option<Result<T>>,
    wakers: Vec<Waker>,
    continuations: Vec<Continuation<T>>,
}

struct Shared<T> {
    state: Mutex<TaskState<T>>,
    done: Condvar,
}

impl<T: Clone> Shared<T> {
    fn new() -> Arc<Shared<T>> {
        Arc::new(Shared {
            state: Mutex::new(TaskState {
                outcome: None,
                wakers: Vec::new(),
                continuations: Vec::new(),
            }),
            done: Condvar::new(),
        })
    }

    /// Sets the outcome once; false when there was one. What awaits the task wakes, and its
    /// continuations run here, outside the lock.
    fn finish(&self, outcome: Result<T>) -> bool {
        let (wakers, continuations, copy) = {
            let mut state = lock(&self.state);
            if state.outcome.is_some() {
                return false;
            }
            let copy = (!state.continuations.is_empty()).then(|| outcome.clone());
            state.outcome = Some(outcome);
            (std::mem::take(&mut state.wakers), std::mem::take(&mut state.continuations), copy)
        };
        self.done.notify_all();
        for waker in wakers {
            waker.wake();
        }
        if let Some(copy) = copy {
            for continuation in continuations {
                continuation(&copy);
            }
        }
        true
    }
}

/// .NET's Task<T> (`Task<()>` for a Task): a spawned future's outcome, or a completion
/// source's. Awaiting it gives the outcome, cloned.
pub struct Task<T> {
    shared: Arc<Shared<T>>,
}

impl<T> Clone for Task<T> {
    fn clone(&self) -> Task<T> {
        Task { shared: Arc::clone(&self.shared) }
    }
}

impl<T: Clone + Send + 'static> Task<T> {
    /// Task.FromResult.
    pub fn from_result(value: T) -> Task<T> {
        let source = TaskCompletionSource::new();
        source.set_result(value);
        source.task()
    }

    /// A task that failed with `error` (Task.FromException; canceled for a cancellation).
    pub fn from_error(error: Exception) -> Task<T> {
        let source = TaskCompletionSource::new();
        source.set_exception(error);
        source.task()
    }

    pub fn is_completed(&self) -> bool {
        lock(&self.shared.state).outcome.is_some()
    }

    pub fn is_faulted(&self) -> bool {
        matches!(&lock(&self.shared.state).outcome, Some(Err(e)) if !e.is_cancellation())
    }

    pub fn is_canceled(&self) -> bool {
        matches!(&lock(&self.shared.state).outcome, Some(Err(e)) if e.is_cancellation())
    }

    /// Waits for the outcome on a thread that isn't one the task needs (a test's).
    pub fn wait(&self) -> Result<T> {
        let mut state = lock(&self.shared.state);
        while state.outcome.is_none() {
            state = self.shared.done.wait(state).unwrap_or_else(|p| p.into_inner());
        }
        state.outcome.clone().unwrap_or_else(|| Err(Exception::task_canceled()))
    }

    /// Runs `f` with the outcome once there is one, on the thread that completes the task (at
    /// once if it is done).
    pub fn on_done(&self, f: impl FnOnce(&Result<T>) + Send + 'static) {
        let mut state = lock(&self.shared.state);
        if let Some(outcome) = state.outcome.clone() {
            drop(state);
            f(&outcome);
            return;
        }
        state.continuations.push(Box::new(f));
    }
}

impl<T: Clone> Future for Task<T> {
    type Output = Result<T>;

    fn poll(self: Pin<&mut Self>, context: &mut Context<'_>) -> Poll<Result<T>> {
        let mut state = lock(&self.shared.state);
        if let Some(outcome) = &state.outcome {
            return Poll::Ready(outcome.clone());
        }
        if !state.wakers.iter().any(|w| w.will_wake(context.waker())) {
            state.wakers.push(context.waker().clone());
        }
        Poll::Pending
    }
}

/// .NET's TaskCompletionSource<T>: a task completed by hand, once.
pub struct TaskCompletionSource<T> {
    shared: Arc<Shared<T>>,
}

impl<T: Clone + Send + 'static> Default for TaskCompletionSource<T> {
    fn default() -> TaskCompletionSource<T> {
        TaskCompletionSource::new()
    }
}

impl<T: Clone + Send + 'static> TaskCompletionSource<T> {
    pub fn new() -> TaskCompletionSource<T> {
        TaskCompletionSource { shared: Shared::new() }
    }

    pub fn task(&self) -> Task<T> {
        Task { shared: Arc::clone(&self.shared) }
    }

    pub fn set_result(&self, value: T) -> bool {
        self.shared.finish(Ok(value))
    }

    /// Fails the task; an OperationCanceledException cancels it.
    pub fn set_exception(&self, error: Exception) -> bool {
        self.shared.finish(Err(error))
    }

    pub fn set_canceled(&self) -> bool {
        self.shared.finish(Err(Exception::task_canceled()))
    }

    /// TrySetCanceled(token): awaiting the task gives a TaskCanceledException with the token.
    pub fn set_canceled_by(&self, token: &CancellationToken) -> bool {
        self.shared.finish(Err(Exception::task_canceled_by(token)))
    }

    pub fn set(&self, outcome: Result<T>) -> bool {
        self.shared.finish(outcome)
    }
}

// ------------------------------------------------------------------------------- the runtime

type BoxedFuture = Pin<Box<dyn Future<Output = ()> + Send>>;

struct Job {
    future: Mutex<Option<BoxedFuture>>,
}

impl Job {
    fn run(self: &Arc<Self>) {
        let waker = Waker::from(Arc::clone(self));
        let mut context = Context::from_waker(&waker);
        let mut slot = lock(&self.future);
        if let Some(future) = slot.as_mut() {
            if future.as_mut().poll(&mut context).is_ready() {
                *slot = None;
            }
        }
    }
}

impl Wake for Job {
    fn wake(self: Arc<Self>) {
        pool().post(Box::new(move || self.run()));
    }
}

type Work = Box<dyn FnOnce() + Send>;

/// The runtime's threads: .NET's thread pool's part. Never stopped (a native library can't
/// join threads while its process exits).
struct Pool {
    queue: Mutex<Vec<Work>>,
    ready: Condvar,
}

impl Pool {
    fn post(&self, work: Work) {
        lock(&self.queue).push(work);
        self.ready.notify_one();
    }

    fn work(&self) {
        loop {
            let job = {
                let mut queue = lock(&self.queue);
                while queue.is_empty() {
                    queue = self.ready.wait(queue).unwrap_or_else(|p| p.into_inner());
                }
                queue.remove(0)
            };
            // A panic in ported code ends its task, never the pool's thread.
            let _ = catch_unwind(AssertUnwindSafe(job));
        }
    }
}

fn pool() -> &'static Pool {
    static POOL: OnceLock<&'static Pool> = OnceLock::new();
    POOL.get_or_init(|| {
        let pool: &'static Pool = Box::leak(Box::new(Pool { queue: Mutex::new(Vec::new()), ready: Condvar::new() }));
        let count = thread::available_parallelism().map_or(2, |n| n.get().max(2));
        for _ in 0..count {
            thread::spawn(move || pool.work());
        }
        pool
    })
}

/// The exception a panic stands for.
fn panic_exception(payload: &(dyn Any + Send)) -> Exception {
    if let Some(e) = payload.downcast_ref::<Exception>() {
        return e.clone();
    }
    let message = payload
        .downcast_ref::<&str>()
        .map(|s| (*s).to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "a Rust panic".to_string());
    Exception::new("System.Exception", message)
}

/// Starts an async C# method's body: it runs on this thread until its first `.await` of
/// something not done yet, then on the runtime's threads; its outcome is the task's.
pub fn spawn<T, F>(body: F) -> Task<T>
where
    T: Clone + Send + 'static,
    F: Future<Output = Result<T>> + Send + 'static,
{
    let source = TaskCompletionSource::new();
    let task = source.task();
    let guarded = async move {
        let mut body = Box::pin(body);
        let outcome = std::future::poll_fn(move |context| {
            match catch_unwind(AssertUnwindSafe(|| body.as_mut().poll(context))) {
                Ok(poll) => poll,
                Err(payload) => Poll::Ready(Err(panic_exception(payload.as_ref()))),
            }
        })
        .await;
        source.set(outcome);
    };
    let job = Arc::new(Job { future: Mutex::new(Some(Box::pin(guarded))) });
    job.run();
    task
}

/// Task.WhenAll: each task's result, in their order, once every one is done; if any failed,
/// the first that did, in their order, as awaiting Task.WhenAll throws.
pub fn when_all<T: Clone + Send + 'static>(tasks: Vec<Task<T>>) -> Task<Vec<T>> {
    spawn(async move {
        let mut results = Vec::with_capacity(tasks.len());
        let mut first: Option<Exception> = None;
        for task in tasks {
            match task.await {
                Ok(value) => results.push(value),
                Err(e) => {
                    if first.is_none() {
                        first = Some(e);
                    }
                }
            }
        }
        match first {
            Some(e) => Err(e),
            None => Ok(results),
        }
    })
}

type Due = Box<dyn FnOnce() + Send>;

/// One thread for every delay's deadline, which hands each due one to the pool.
struct Timer {
    due: Mutex<BTreeMap<(Instant, u64), Due>>,
    changed: Condvar,
    next: AtomicU64,
}

impl Timer {
    fn at(&self, when: Instant, work: Due) {
        let id = self.next.fetch_add(1, Ordering::Relaxed);
        lock(&self.due).insert((when, id), work);
        self.changed.notify_one();
    }

    fn run(&self) {
        let mut due = lock(&self.due);
        loop {
            let Some((&(when, id), _)) = due.iter().next() else {
                due = self.changed.wait(due).unwrap_or_else(|p| p.into_inner());
                continue;
            };
            let now = Instant::now();
            if now < when {
                due = self.changed.wait_timeout(due, when - now).unwrap_or_else(|p| p.into_inner()).0;
                continue;
            }
            if let Some(work) = due.remove(&(when, id)) {
                pool().post(work);
            }
        }
    }
}

fn timer() -> &'static Timer {
    static TIMER: OnceLock<&'static Timer> = OnceLock::new();
    TIMER.get_or_init(|| {
        let timer: &'static Timer = Box::leak(Box::new(Timer {
            due: Mutex::new(BTreeMap::new()),
            changed: Condvar::new(),
            next: AtomicU64::new(0),
        }));
        thread::spawn(move || timer.run());
        timer
    })
}

/// Task.Delay: done after `duration` (None, Timeout.Infinite, never), canceled with the token,
/// a TaskCanceledException, as .NET's.
pub fn delay(duration: Option<Duration>, cancellation: &CancellationToken) -> Task<()> {
    if cancellation.is_cancellation_requested() {
        return Task::from_error(Exception::task_canceled_by(cancellation));
    }
    let source = Arc::new(TaskCompletionSource::new());
    if let Some(duration) = duration {
        let done = Arc::clone(&source);
        timer().at(Instant::now() + duration, Box::new(move || {
            done.set_result(());
        }));
    }
    let canceled = Arc::clone(&source);
    // The callback holds the token's state weakly, as the state holds the callback.
    let weak = cancellation.state.as_ref().map(Arc::downgrade);
    let registration = cancellation.register(move || {
        let token = CancellationToken { state: weak.as_ref().and_then(std::sync::Weak::upgrade) };
        canceled.set_canceled_by(&token);
    });
    let task = source.task();
    task.on_done(move |_| drop(registration));
    task
}
