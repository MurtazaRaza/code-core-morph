//! The Rust side of a C# façade's bridge: statuses, the error record, strings, arrays,
//! decimals, handles and calls into C#. Written by CodeCoreMorph; do not edit. Only the bridge
//! uses it, and the retirement deletes it with the bridge.
//!
//! Its C types are those of `codecoremorph_bridge.h`, which cbindgen's header includes rather
//! than declares again. Every export runs its body in [`guard`]: an [`Exception`] the body
//! returns, or a panic, becomes a status and the error the façade throws; nothing unwinds into
//! .NET.
#![allow(non_camel_case_types, dead_code)]

use std::any::Any;
use std::cell::RefCell;
use std::collections::HashMap;
use std::ffi::{c_char, c_void, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

use codecoremorph_runtime::{Decimal, Exception};

/// What every export returns: [`CCM_OK`], [`CCM_ERROR`] (the façade reads the error) or
/// [`CCM_MANAGED`] (a call into C# threw; the façade rethrows that exception).
pub type ccm_status = i32;
pub const CCM_OK: ccm_status = 0;
pub const CCM_ERROR: ccm_status = 1;
pub const CCM_MANAGED: ccm_status = 2;

/// A .NET decimal, as `decimal.GetBits` gives it.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct ccm_decimal {
    pub lo: i32,
    pub mid: i32,
    pub hi: i32,
    pub flags: i32,
}

/// Frees what a context pointer holds (a GCHandle), when the native side lets go of it.
pub type ccm_release_fn = Option<unsafe extern "C" fn(context: *mut c_void)>;

unsafe extern "C" {
    fn malloc(size: usize) -> *mut c_void;
    fn free(memory: *mut c_void);
}

// ------------------------------------------------------------------ errors

thread_local! {
    static ERROR: RefCell<[CString; 4]> = RefCell::new(Default::default());
}

fn c_string(text: &str) -> CString {
    CString::new(text.replace('\0', " ")).unwrap_or_default()
}

fn report(e: &Exception) -> ccm_status {
    if e.is_managed() {
        return CCM_MANAGED;
    }
    let mut fields = String::new();
    for (name, value) in e.fields() {
        fields.push_str(name);
        fields.push('=');
        fields.push_str(&value.replace('\\', "\\\\").replace('\n', "\\n"));
        fields.push('\n');
    }
    ERROR.with(|error| {
        *error.borrow_mut() = [
            c_string(e.dotnet_type()),
            c_string(e.message()),
            c_string(e.param_name().unwrap_or("")),
            c_string(&fields),
        ];
    });
    CCM_ERROR
}

/// The exception a panic stands for: the one it carries (`panic_any`, as the decimal's
/// operators do), or what .NET throws for the same mistake.
fn from_panic(payload: Box<dyn Any + Send>) -> Exception {
    let payload = match payload.downcast::<Exception>() {
        Ok(e) => return *e,
        Err(other) => other,
    };
    let message = payload
        .downcast_ref::<&str>()
        .map(|s| (*s).to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "a Rust panic".to_string());
    if message.contains("divide by zero") || message.contains("divisor of zero") {
        Exception::divide_by_zero()
    } else if message.contains("overflow") {
        Exception::overflow("Arithmetic operation resulted in an overflow.")
    } else if message.contains("index out of bounds") {
        Exception::index_out_of_range("Index was outside the bounds of the array.")
    } else {
        Exception::new("System.Exception", message)
    }
}

/// Runs an export's body: an error or a panic becomes its status, never an unwind into .NET.
pub fn guard(body: impl FnOnce() -> Result<(), Exception>) -> ccm_status {
    match catch_unwind(AssertUnwindSafe(body)) {
        Ok(Ok(())) => CCM_OK,
        Ok(Err(e)) => report(&e),
        Err(payload) => report(&from_panic(payload)),
    }
}

/// The part of the calling thread's last error: 0 the .NET type, 1 the message, 2 the
/// parameter name, 3 the fields. Valid until the thread's next export call.
#[unsafe(no_mangle)]
pub extern "C" fn ccm_error_part(part: i32) -> *const c_char {
    ERROR.with(|error| error.borrow()[part.clamp(0, 3) as usize].as_ptr())
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn ccm_free(memory: *mut c_void) {
    // SAFETY: memory this library malloc'd (a string or an array out), or null.
    unsafe { free(memory) }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn ccm_strdup(text: *const c_char) -> *mut c_char {
    if text.is_null() {
        return std::ptr::null_mut();
    }
    // SAFETY: C# passes NUL-terminated UTF-8.
    dup(&unsafe { CStr::from_ptr(text) }.to_string_lossy())
}

// ------------------------------------------------------------------ out parameters

/// Writes an out parameter's value.
pub fn put<T>(out: *mut T, value: T) {
    if !out.is_null() {
        // SAFETY: the façade passes a pointer to its own local for each out parameter.
        unsafe { out.write(value) }
    }
}

// ------------------------------------------------------------------ strings

/// A string parameter C# requires: null throws ArgumentNullException, as the C# check did.
pub fn str_in(text: *const c_char, param_name: &str) -> Result<String, Exception> {
    opt_str(text).ok_or_else(|| Exception::argument_null(param_name))
}

/// A string parameter that may be null.
pub fn opt_str(text: *const c_char) -> Option<String> {
    if text.is_null() {
        return None;
    }
    // SAFETY: the façade passes NUL-terminated UTF-8 that lives for the call.
    Some(unsafe { CStr::from_ptr(text) }.to_string_lossy().into_owned())
}

fn dup(text: &str) -> *mut c_char {
    let bytes = text.as_bytes();
    // SAFETY: malloc'd for the façade, which frees it with ccm_free; NUL-terminated.
    unsafe {
        let copy = malloc(bytes.len() + 1).cast::<u8>();
        if copy.is_null() {
            std::alloc::handle_alloc_error(std::alloc::Layout::array::<u8>(bytes.len() + 1).unwrap());
        }
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), copy, bytes.len());
        copy.add(bytes.len()).write(0);
        copy.cast::<c_char>()
    }
}

/// A string out, which the façade reads and frees.
pub fn out_str(out: *mut *mut c_char, text: &str) {
    put(out, dup(text));
}

/// A string out that may be null.
pub fn out_opt_str(out: *mut *mut c_char, text: Option<&str>) {
    put(out, text.map_or(std::ptr::null_mut(), dup));
}

/// A string C# gave back from a call into it (`CcmInterop.NewString`): read and freed.
pub fn take_str(text: *mut c_char) -> Option<String> {
    let value = opt_str(text);
    if !text.is_null() {
        // SAFETY: C# allocated it with ccm_strdup, so it is this library's to free.
        unsafe { free(text.cast()) }
    }
    value
}

// ------------------------------------------------------------------ arrays

/// An array parameter C# requires: the façade passes a count of -1 for null, which throws
/// ArgumentNullException, as the C# check did.
pub fn array<T: Copy>(data: *const T, count: i32, param_name: &str) -> Result<Vec<T>, Exception> {
    if count < 0 {
        return Err(Exception::argument_null(param_name));
    }
    if count == 0 || data.is_null() {
        return Ok(Vec::new());
    }
    // SAFETY: the façade passes the array's first element and its length.
    Ok(unsafe { std::slice::from_raw_parts(data, count as usize) }.to_vec())
}

/// A string array parameter (`string[]`): its elements may be null.
pub fn strings(
    data: *const *const c_char,
    count: i32,
    param_name: &str,
) -> Result<Vec<Option<String>>, Exception> {
    Ok(array(data, count, param_name)?.into_iter().map(opt_str).collect())
}

/// An array the export returns: a malloc'd copy and its length, which the façade reads and
/// frees (`CcmInterop.TakeBytes` for bytes); never null, so an empty array is one too.
pub fn out_array<T: Copy>(out: *mut *mut T, count: *mut i32, values: &[T]) -> Result<(), Exception> {
    let len = i32::try_from(values.len()).map_err(|_| Exception::overflow("the array is too long"))?;
    let size = std::mem::size_of_val(values).max(1);
    // SAFETY: malloc'd for the façade, which frees it with ccm_free.
    let copy = unsafe { malloc(size) }.cast::<T>();
    if copy.is_null() {
        std::alloc::handle_alloc_error(std::alloc::Layout::array::<u8>(size).unwrap());
    }
    // SAFETY: copy has room for values.len() elements.
    unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), copy, values.len()) };
    put(out, copy);
    put(count, len);
    Ok(())
}

// ------------------------------------------------------------------ decimals

pub fn dec(d: ccm_decimal) -> Result<Decimal, Exception> {
    Decimal::from_bits(d.lo, d.mid, d.hi, d.flags)
}

pub fn dec_out(d: Decimal) -> ccm_decimal {
    let [lo, mid, hi, flags] = d.bits();
    ccm_decimal { lo, mid, hi, flags }
}

// ------------------------------------------------------------------ handles

/// A handle type of the bridge: the opaque struct cbindgen declares, tied to what it holds. A
/// handle is a heap-allocated `Arc` of its target, released by the façade's `SafeHandle`, so
/// the target is `Send + Sync` (.NET's finalizer thread may release it).
pub trait Handle {
    type Target: ?Sized + Send + Sync + 'static;
}

/// A new handle, owned by the receiver, to a shared object.
pub fn to_handle<H: Handle>(object: Arc<H::Target>) -> *mut H {
    Box::into_raw(Box::new(object)).cast::<H>()
}

/// A handle, or null for C#'s null.
pub fn to_handle_or_null<H: Handle>(object: Option<Arc<H::Target>>) -> *mut H {
    object.map_or(std::ptr::null_mut(), to_handle::<H>)
}

/// The object a handle holds. A null handle is C#'s null: using it throws
/// NullReferenceException, as the C# member access did.
pub fn from_handle<'a, H: Handle>(handle: *const H) -> Result<&'a Arc<H::Target>, Exception> {
    if handle.is_null() {
        return Err(Exception::null_reference());
    }
    // SAFETY: a non-null handle is one to_handle made and its SafeHandle hasn't released.
    Ok(unsafe { &*handle.cast::<Arc<H::Target>>() })
}

/// The object, or None for null, where C# allows null.
pub fn from_handle_or_null<H: Handle>(handle: *const H) -> Option<Arc<H::Target>> {
    from_handle(handle).ok().cloned()
}

/// A handle parameter C# requires: null throws ArgumentNullException, as the C# check did.
pub fn required<H: Handle>(handle: *const H, param_name: &str) -> Result<Arc<H::Target>, Exception> {
    from_handle_or_null(handle).ok_or_else(|| Exception::argument_null(param_name))
}

/// A handle's `_release` export.
pub fn release<H: Handle>(handle: *mut H) {
    if !handle.is_null() {
        // SAFETY: the façade's SafeHandle releases each handle once.
        drop(unsafe { Box::from_raw(handle.cast::<Arc<H::Target>>()) });
    }
}

/// A handle's `_key` export: the object's identity, the address of its data whatever the
/// handle's type, so a trait object's handle and its type's have the same key.
pub fn key<H: Handle>(handle: *const H) -> u64 {
    from_handle(handle).map_or(0, |object| Arc::as_ptr(object).cast::<()>() as usize as u64)
}

/// Locks an object a handle holds behind a `Mutex`; a lock that a panic poisoned still holds
/// the object. Never hold it across a call into C#: C# may call back into the same object.
pub fn lock<T: ?Sized>(object: &Mutex<T>) -> MutexGuard<'_, T> {
    object.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

// ------------------------------------------------------------------ calls into C#

/// A C# object native code holds (a GCHandle), released when the last clone goes.
pub struct ManagedRef {
    context: *mut c_void,
    release: ccm_release_fn,
}

// SAFETY: a GCHandle may be used and freed from any thread.
unsafe impl Send for ManagedRef {}
unsafe impl Sync for ManagedRef {}

impl ManagedRef {
    pub fn new(context: *mut c_void, release: ccm_release_fn) -> Arc<ManagedRef> {
        Arc::new(ManagedRef { context, release })
    }

    pub fn context(&self) -> *mut c_void {
        self.context
    }
}

impl Drop for ManagedRef {
    fn drop(&mut self) {
        // One condition, not two nested ifs: clippy asks for a let chain on edition 2024,
        // which earlier editions lack.
        if let Some(release) = self.release.filter(|_| !self.context.is_null()) {
            // SAFETY: the release C# gave with the context, called once.
            unsafe { release(self.context) }
        }
    }
}

/// What a call into C# returned: [`CCM_MANAGED`] (its exception) becomes
/// [`Exception::managed`], which the export passes back so the façade rethrows it.
pub fn check_managed(status: ccm_status) -> Result<(), Exception> {
    if status == CCM_OK {
        Ok(())
    } else {
        Err(Exception::managed())
    }
}

/// A callback C# passed: null is a C# delegate that was null, and calling it throws
/// NullReferenceException, as invoking the null delegate did.
pub fn callback<F: Copy>(f: Option<F>) -> Result<F, Exception> {
    f.ok_or_else(Exception::null_reference)
}

fn managed_objects() -> &'static Mutex<HashMap<usize, usize>> {
    static OBJECTS: OnceLock<Mutex<HashMap<usize, usize>>> = OnceLock::new();
    OBJECTS.get_or_init(Default::default)
}

/// Records that the native object at `address` (a proxy implementing an interface over a C#
/// object) stands for the C# object `context`, for its `_managed` export.
pub fn register_managed(address: usize, context: *mut c_void) {
    lock(managed_objects()).insert(address, context as usize);
}

/// The proxy at `address` is going.
pub fn unregister_managed(address: usize) {
    lock(managed_objects()).remove(&address);
}

/// The C# object a proxy stands for, or null for a native object.
pub fn managed_of(address: usize) -> *mut c_void {
    lock(managed_objects()).get(&address).map_or(std::ptr::null_mut(), |&c| c as *mut c_void)
}
