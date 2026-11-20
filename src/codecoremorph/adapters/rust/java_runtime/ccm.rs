//! The Rust side of a Java façade's bridge: statuses, the error record, strings, arrays, handles
//! and calls into Java. Written by CodeCoreMorph; do not edit. Every export runs its body in
//! [`guard`]: an [`Exception`] the body returns, or a panic, becomes a status and the error the
//! façade throws; nothing unwinds into the JVM. Write a handle type's `_release` and `_key`
//! exports out (cbindgen expands no macro): `ccm::release(handle)` and `ccm::key(handle)`.
#![allow(non_camel_case_types, dead_code)]

use std::any::Any;
use std::cell::RefCell;
use std::ffi::{c_char, c_void, CStr, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Arc;

use codecoremorph_java::Exception;

pub type ccm_status = i32;
pub const CCM_OK: ccm_status = 0;
pub const CCM_ERROR: ccm_status = 1;
pub const CCM_MANAGED: ccm_status = 2;

pub type ccm_release_fn = Option<unsafe extern "C" fn(context: *mut c_void)>;

unsafe extern "C" {
    fn malloc(size: usize) -> *mut c_void;
    fn free(memory: *mut c_void);
}

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
        *error.borrow_mut() =
            [c_string(e.java_type()), c_string(e.message().unwrap_or("")), CString::default(), c_string(&fields)];
    });
    CCM_ERROR
}

/// The exception a panic stands for: what Java throws for the same mistake. An overflow panic is
/// a port's bug (Java wraps), reported so the test that hit it fails.
fn from_panic(payload: Box<dyn Any + Send>) -> Exception {
    let message = payload
        .downcast_ref::<&str>()
        .map(|s| (*s).to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "a Rust panic".to_string());
    if message.contains("divide by zero") || message.contains("remainder with a divisor of zero") {
        Exception::arithmetic("/ by zero")
    } else if message.contains("index out of bounds") {
        Exception::new("java.lang.ArrayIndexOutOfBoundsException", message)
    } else {
        Exception::new("java.lang.RuntimeException", format!("a Rust panic: {message}"))
    }
}

/// Runs an export's body: an error or a panic becomes its status, never an unwind into Java.
pub fn guard(body: impl FnOnce() -> Result<(), Exception>) -> ccm_status {
    match catch_unwind(AssertUnwindSafe(body)) {
        Ok(Ok(())) => CCM_OK,
        Ok(Err(e)) => report(&e),
        Err(payload) => report(&from_panic(payload)),
    }
}

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
    // SAFETY: NUL-terminated UTF-8 from the glue.
    dup(&unsafe { CStr::from_ptr(text) }.to_string_lossy())
}

/// Writes an out parameter's value.
pub fn put<T>(out: *mut T, value: T) {
    if !out.is_null() {
        // SAFETY: the glue passes a pointer to its own local for each out parameter.
        unsafe { out.write(value) }
    }
}

/// A string the Java dereferences: null is NullPointerException, with the message the Java's
/// own check gave, if any.
pub fn str_in(text: *const c_char, message: Option<&str>) -> Result<String, Exception> {
    opt_str(text).ok_or_else(|| Exception::null_pointer(message))
}

pub fn opt_str(text: *const c_char) -> Option<String> {
    if text.is_null() {
        return None;
    }
    // SAFETY: the glue passes NUL-terminated UTF-8 that lives for the call.
    Some(unsafe { CStr::from_ptr(text) }.to_string_lossy().into_owned())
}

fn dup(text: &str) -> *mut c_char {
    let bytes = text.as_bytes();
    // SAFETY: malloc'd for the glue, which frees it with ccm_free; NUL-terminated.
    unsafe {
        let copy = malloc(bytes.len() + 1).cast::<u8>();
        assert!(!copy.is_null(), "out of memory");
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), copy, bytes.len());
        copy.add(bytes.len()).write(0);
        copy.cast::<c_char>()
    }
}

pub fn out_str(out: *mut *mut c_char, text: &str) {
    put(out, dup(text));
}

/// A string a call into Java gave back: read and freed.
pub fn take_str(text: *mut c_char) -> Option<String> {
    let value = opt_str(text);
    if !text.is_null() {
        // SAFETY: the glue malloc'd it.
        unsafe { free(text.cast()) }
    }
    value
}

/// An array in: a count of -1 is Java's null, NullPointerException where the Java used it.
pub fn array<T: Copy>(data: *const T, count: i32) -> Result<Vec<T>, Exception> {
    if count < 0 {
        return Err(Exception::null_pointer(None));
    }
    if count == 0 || data.is_null() {
        return Ok(Vec::new());
    }
    // SAFETY: the glue passes `count` elements.
    Ok(unsafe { std::slice::from_raw_parts(data, count as usize) }.to_vec())
}

/// Strings in, each maybe null.
pub fn strings(data: *const *const c_char, count: i32) -> Result<Vec<Option<String>>, Exception> {
    Ok(array(data, count)?.into_iter().map(opt_str).collect())
}

/// An array out: a malloc'd copy, which the glue copies into a Java array and frees.
pub fn out_array<T: Copy>(out: *mut *mut T, count: *mut i32, values: &[T]) {
    let size = std::mem::size_of_val(values).max(1);
    // SAFETY: malloc'd for the glue, which frees it with ccm_free.
    unsafe {
        let copy = malloc(size).cast::<T>();
        assert!(!copy.is_null(), "out of memory");
        std::ptr::copy_nonoverlapping(values.as_ptr(), copy, values.len());
        put(out, copy);
    }
    put(count, values.len() as i32);
}

// ------------------------------------------------------------------ handles

/// A handle type: an opaque struct whose pointer holds a `Box<Arc<Target>>`.
pub trait Handle {
    type Target: ?Sized;
}

pub fn to_handle<H: Handle>(object: Arc<H::Target>) -> *mut H {
    Box::into_raw(Box::new(object)).cast()
}

/// The object a handle holds; a null handle is Java's null: NullPointerException.
pub fn from_handle<'a, H: Handle>(handle: *const H) -> Result<&'a Arc<H::Target>, Exception> {
    if handle.is_null() {
        return Err(Exception::null_pointer(None));
    }
    // SAFETY: a handle to_handle made and the façade hasn't released.
    Ok(unsafe { &*handle.cast::<Arc<H::Target>>() })
}

pub fn from_handle_or_null<H: Handle>(handle: *const H) -> Option<Arc<H::Target>> {
    from_handle(handle).ok().cloned()
}

pub fn release<H: Handle>(handle: *mut H) {
    if !handle.is_null() {
        // SAFETY: the façade releases each handle once.
        drop(unsafe { Box::from_raw(handle.cast::<Arc<H::Target>>()) });
    }
}

pub fn key<H: Handle>(handle: *const H) -> u64 {
    from_handle(handle).map_or(0, |arc| Arc::as_ptr(arc).cast::<()>() as usize as u64)
}

// ------------------------------------------------------------------ calls into Java

/// A Java object native code holds (a JNI global reference), released when dropped.
pub struct ManagedRef {
    context: *mut c_void,
    release: ccm_release_fn,
}

// SAFETY: a global reference may be used and deleted from any thread attached to the JVM.
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
        if let (Some(release), false) = (self.release, self.context.is_null()) {
            // SAFETY: the release the glue passed for this context.
            unsafe { release(self.context) }
        }
    }
}

/// What a call into Java returned: anything but CCM_OK is the Java's exception, which the
/// façade rethrows.
pub fn check_managed(status: ccm_status) -> Result<(), Exception> {
    if status == CCM_OK { Ok(()) } else { Err(Exception::managed()) }
}

/// A function pointer the glue passed; null is NullPointerException.
pub fn callback<F: Copy>(f: Option<F>) -> Result<F, Exception> {
    f.ok_or_else(|| Exception::null_pointer(None))
}
