//! The bridge's side of .NET's tasks (D-96). Written by CodeCoreMorph; do not edit; the
//! retirement deletes it with the bridge.
//!
//! A façade's async member returns the C# task its export completes: the export takes the
//! task's context (`task`, the façades' support's) and [`start`]s the native task, which, when
//! done, calls the support's completion for its kind of result, once, from the thread that
//! finished it: CCM_OK and the result; CCM_ERROR, the error's parts readable during the call (a
//! canceled task's is a TaskCanceledException); or CCM_MANAGED, the C# exception's handle
//! readable during the call. A cancellation token crosses as a `*mut c_void` (null for
//! CancellationToken.None), borrowed for the call. A C# task native code awaits
//! ([`managed_task`]) completes the native source it was given, once.
#![allow(dead_code)]
use std::ffi::{c_char, c_void, CString};
use std::sync::OnceLock;
use std::time::Duration;

use codecoremorph_runtime::tasks::{CancellationToken, CancellationTokenSource, Task, TaskCompletionSource};
use codecoremorph_runtime::{Exception, Result};

use super::ccm::{self, ccm_release_fn, ccm_status, ManagedRef, CCM_OK};

/// A C# task canceled: the status a C# task's source is completed with.
pub const CCM_CANCELED: ccm_status = 3;

pub type ccm_done_fn = Option<unsafe extern "C" fn(task: *mut c_void, status: ccm_status)>;
pub type ccm_done_string_fn =
    Option<unsafe extern "C" fn(task: *mut c_void, status: ccm_status, result: *const c_char)>;
pub type ccm_done_strings_fn = Option<
    unsafe extern "C" fn(task: *mut c_void, status: ccm_status, result: *const *const c_char, count: i32),
>;
pub type ccm_done_int64_fn = Option<unsafe extern "C" fn(task: *mut c_void, status: ccm_status, result: i64)>;
pub type ccm_done_double_fn = Option<unsafe extern "C" fn(task: *mut c_void, status: ccm_status, result: f64)>;
pub type ccm_done_handle_fn =
    Option<unsafe extern "C" fn(task: *mut c_void, status: ccm_status, result: *mut c_void)>;
pub type ccm_cancel_fn = Option<unsafe extern "C" fn(context: *mut c_void)>;

struct Completions {
    done: ccm_done_fn,
    string: ccm_done_string_fn,
    strings: ccm_done_strings_fn,
    int64: ccm_done_int64_fn,
    double: ccm_done_double_fn,
}

static COMPLETIONS: OnceLock<Completions> = OnceLock::new();

/// The support's completions, registered once before the first task.
#[unsafe(no_mangle)]
pub extern "C" fn ccm_tasks_init(
    done: ccm_done_fn,
    string: ccm_done_string_fn,
    strings: ccm_done_strings_fn,
    int64: ccm_done_int64_fn,
    double: ccm_done_double_fn,
    _handle: ccm_done_handle_fn,
) {
    let _ = COMPLETIONS.set(Completions { done, string, strings, int64, double });
}

// ------------------------------------------------------------------------------- tokens

/// A token as it crosses: a C#-made one's source (which the façade cancels), or a native
/// one's token, for a call into C#.
struct TokenBox {
    token: CancellationToken,
    source: Option<CancellationTokenSource>,
}

fn token_box<'a>(token: *const c_void) -> Option<&'a TokenBox> {
    // SAFETY: a non-null token is a TokenBox ccm_token_new or TokenOut made, alive for the call.
    (!token.is_null()).then(|| unsafe { &*token.cast::<TokenBox>() })
}

/// A native token for a C# one (`origin`: a GCHandle of the boxed C# token, released with the
/// token's state).
#[unsafe(no_mangle)]
pub extern "C" fn ccm_token_new(origin: *mut c_void, release: ccm_release_fn) -> *mut c_void {
    let source = CancellationTokenSource::with_origin(ManagedRef::new(origin, release));
    let token = source.token();
    Box::into_raw(Box::new(TokenBox { token, source: Some(source) })).cast()
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_token_cancel(token: *mut c_void) {
    if let Some(source) = token_box(token).and_then(|b| b.source.as_ref()) {
        source.cancel();
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_token_release(token: *mut c_void) {
    if !token.is_null() {
        // SAFETY: the façade releases the token ccm_token_new gave it, once.
        drop(unsafe { Box::from_raw(token.cast::<TokenBox>()) });
    }
}

/// The C# token a native one stands for, or null for one native code made.
#[unsafe(no_mangle)]
pub extern "C" fn ccm_token_origin(token: *const c_void) -> *mut c_void {
    token_box(token)
        .and_then(|b| b.token.origin())
        .and_then(|o| o.downcast::<ManagedRef>().ok())
        .map_or(std::ptr::null_mut(), |m| m.context())
}

/// For a native token native code made: `cancel(context)` when it is canceled.
#[unsafe(no_mangle)]
pub extern "C" fn ccm_token_on_cancel(
    token: *const c_void,
    cancel: ccm_cancel_fn,
    context: *mut c_void,
    release: ccm_release_fn,
) {
    let managed = ManagedRef::new(context, release);
    let (Some(boxed), Some(cancel)) = (token_box(token), cancel) else { return };
    let registration = boxed.token.register(move || {
        // SAFETY: the support's callback, with the context it gave.
        unsafe { cancel(managed.context()) }
    });
    std::mem::forget(registration); // it lasts as long as the token's state
}

/// The native token a C# one crossed as (CancellationToken.None for null).
pub fn token(cancellation: *mut c_void) -> CancellationToken {
    token_box(cancellation).map_or_else(CancellationToken::none, |b| b.token.clone())
}

/// A native token as a call into C# passes it: a pointer valid while this lives.
pub struct TokenOut(*mut c_void);

impl TokenOut {
    pub fn new(token: &CancellationToken) -> TokenOut {
        if !token.can_be_canceled() {
            return TokenOut(std::ptr::null_mut());
        }
        TokenOut(Box::into_raw(Box::new(TokenBox { token: token.clone(), source: None })).cast())
    }

    pub fn get(&self) -> *mut c_void {
        self.0
    }
}

impl Drop for TokenOut {
    fn drop(&mut self) {
        ccm_token_release(self.0);
    }
}

/// TimeSpan's ticks (100 ns) as the whole milliseconds Task.Delay waits, as .NET's.
pub fn milliseconds(ticks: i64) -> Option<Duration> {
    u64::try_from(ticks / 10_000).ok().map(Duration::from_millis)
}

// ------------------------------------------------------------------------------- completions

/// What a native task's result is to the support's completion.
pub trait Outcome: Clone + Send + 'static {
    fn complete(context: *mut c_void, outcome: &Result<Self>);
}

fn status_of(error: &Exception) -> ccm_status {
    ccm::guard(|| Err(error.clone()))
}

fn completions() -> Option<&'static Completions> {
    COMPLETIONS.get()
}

impl Outcome for () {
    fn complete(context: *mut c_void, outcome: &Result<()>) {
        let status = outcome.as_ref().err().map_or(CCM_OK, status_of);
        if let Some(done) = completions().and_then(|c| c.done) {
            // SAFETY: the support's completion, given the context it made.
            unsafe { done(context, status) }
        }
    }
}

fn complete_string(context: *mut c_void, status: ccm_status, value: Option<&str>) {
    let Some(done) = completions().and_then(|c| c.string) else { return };
    let text = value.map(|v| CString::new(v.replace('\0', " ")).unwrap_or_default());
    let pointer = text.as_ref().map_or(std::ptr::null(), |t| t.as_ptr());
    // SAFETY: as above; the string lives through the call.
    unsafe { done(context, status, pointer) }
}

impl Outcome for String {
    fn complete(context: *mut c_void, outcome: &Result<String>) {
        match outcome {
            Ok(value) => complete_string(context, CCM_OK, Some(value)),
            Err(e) => complete_string(context, status_of(e), None),
        }
    }
}

impl Outcome for Option<String> {
    fn complete(context: *mut c_void, outcome: &Result<Option<String>>) {
        match outcome {
            Ok(value) => complete_string(context, CCM_OK, value.as_deref()),
            Err(e) => complete_string(context, status_of(e), None),
        }
    }
}

impl Outcome for Vec<String> {
    fn complete(context: *mut c_void, outcome: &Result<Vec<String>>) {
        let Some(done) = completions().and_then(|c| c.strings) else { return };
        let (status, texts) = match outcome {
            Ok(values) => (
                CCM_OK,
                values.iter().map(|v| CString::new(v.replace('\0', " ")).unwrap_or_default()).collect(),
            ),
            Err(e) => (status_of(e), Vec::new()),
        };
        let pointers: Vec<*const c_char> = texts.iter().map(|t| t.as_ptr()).collect();
        let count = i32::try_from(pointers.len()).unwrap_or(i32::MAX);
        // SAFETY: as above; the strings and the array live through the call.
        unsafe { done(context, status, pointers.as_ptr(), count) }
    }
}

macro_rules! integer_outcomes {
    ($($t:ty),*) => {
        $(impl Outcome for $t {
            fn complete(context: *mut c_void, outcome: &Result<$t>) {
                let Some(done) = completions().and_then(|c| c.int64) else { return };
                let (status, value) = match outcome {
                    Ok(v) => (CCM_OK, i64::from(*v)),
                    Err(e) => (status_of(e), 0),
                };
                // SAFETY: as above.
                unsafe { done(context, status, value) }
            }
        })*
    };
}

integer_outcomes!(i32, i64, bool);

impl Outcome for f64 {
    fn complete(context: *mut c_void, outcome: &Result<f64>) {
        let Some(done) = completions().and_then(|c| c.double) else { return };
        let (status, value) = match outcome {
            Ok(v) => (CCM_OK, *v),
            Err(e) => (status_of(e), 0.0),
        };
        // SAFETY: as above.
        unsafe { done(context, status, value) }
    }
}

/// An async export's body: starts the native task `body` gives and completes the façade's C#
/// task (`context`) with its outcome. An error `body` returns faults the task, as an async C#
/// method's argument checks do; a task done at once completes the C# one before the export
/// returns.
pub fn start<T: Outcome>(context: *mut c_void, body: impl FnOnce() -> Result<Task<T>>) {
    // The context goes to another thread as an address: it is the support's, for one call.
    let address = context as usize;
    match body() {
        Ok(task) => task.on_done(move |outcome| T::complete(address as *mut c_void, outcome)),
        Err(e) => T::complete(context, &Err(e)),
    }
}

/// A C# task native code awaits: `call` makes the call into C#, given the native source the
/// support completes, and returns its status; a C# exception the call threw at once is the
/// task's, by its types.
pub fn managed_task<T: Clone + Send + 'static>(call: impl FnOnce(*mut c_void) -> ccm_status) -> Task<T> {
    let source = Box::into_raw(Box::new(TaskCompletionSource::<T>::new()));
    // SAFETY: just made; the support takes it on a CCM_OK call, else it is freed below.
    let task = unsafe { &*source }.task();
    let status = call(source.cast());
    if status != CCM_OK {
        // SAFETY: the support didn't take it.
        drop(unsafe { Box::from_raw(source) });
        return Task::from_error(ccm::take_managed());
    }
    task
}

fn finish<T: Clone + Send + 'static>(source: *mut c_void, status: ccm_status, value: impl FnOnce() -> T) {
    if source.is_null() {
        return;
    }
    // SAFETY: the source managed_task gave the support, completed once.
    let source = unsafe { Box::from_raw(source.cast::<TaskCompletionSource<T>>()) };
    match status {
        CCM_OK => source.set_result(value()),
        CCM_CANCELED => source.set_canceled(),
        _ => source.set_exception(ccm::take_managed()),
    };
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_source_done(source: *mut c_void, status: ccm_status) {
    finish::<()>(source, status, || ());
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_source_done_string(source: *mut c_void, status: ccm_status, result: *const c_char) {
    finish::<Option<String>>(source, status, || ccm::opt_str(result));
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_source_done_int64(source: *mut c_void, status: ccm_status, result: i64) {
    finish::<i64>(source, status, || result);
}

#[unsafe(no_mangle)]
pub extern "C" fn ccm_source_done_double(source: *mut c_void, status: ccm_status, result: f64) {
    finish::<f64>(source, status, || result);
}

