// The fixed part of a Java façade's JNI glue in Rust: what the generated glue (`jni_g.rs`)
// calls. Written by CodeCoreMorph; do not edit. Only the bridge uses it, and the retirement
// deletes it with the bridge. The JNI types are bindgen's, from the JDK's `jni.h`
// (`jni_sys.rs`, written at configure).

use std::ffi::{c_char, c_void, CStr, CString};
use std::ptr::null_mut;
use std::sync::atomic::{AtomicPtr, Ordering};

pub use super::jni_sys::{
    jarray, jboolean, jbyte, jclass, jdouble, jfloat, jint, jlong, jobject, jobjectArray, jshort,
    jsize, jvalue, JNIEnv, JavaVM,
};

pub type jbyteArray = jarray;

pub const CCM_OK: i32 = 0;
pub const CCM_ERROR: i32 = 1;
pub const CCM_MANAGED: i32 = 2;
const JNI_VERSION_1_8: jint = 0x0001_0008;

static VM: AtomicPtr<JavaVM> = AtomicPtr::new(null_mut());
static NATIVE: AtomicPtr<c_void> = AtomicPtr::new(null_mut());
static INTEROP: AtomicPtr<c_void> = AtomicPtr::new(null_mut());
static BYTE_ARRAY: AtomicPtr<c_void> = AtomicPtr::new(null_mut());
static MANAGED: Method = Method::new(c"managed", c"(Ljava/lang/Throwable;)I");

unsafe extern "C" {
    fn malloc(size: usize) -> *mut c_void;
    #[link_name = "free"]
    fn c_free(memory: *mut c_void);
}

/// A JNI function of the environment's table.
macro_rules! jni {
    ($env:expr, $name:ident $(, $arg:expr)*) => {
        ((**$env).$name.expect(stringify!($name)))($env $(, $arg)*)
    };
}

#[unsafe(no_mangle)]
pub unsafe extern "system" fn JNI_OnLoad(vm: *mut JavaVM, _reserved: *mut c_void) -> jint {
    unsafe {
        let mut env: *mut JNIEnv = null_mut();
        let get_env = (**vm).GetEnv.expect("GetEnv");
        if get_env(vm, (&mut env as *mut *mut JNIEnv).cast(), JNI_VERSION_1_8) != 0 {
            return -1;
        }
        VM.store(vm, Ordering::SeqCst);
        for (name, slot) in [
            (c"codecoremorph/interop/CcmNative", &NATIVE),
            (c"codecoremorph/interop/CcmInterop", &INTEROP),
            (c"[B", &BYTE_ARRAY),
        ] {
            let class = jni!(env, FindClass, name.as_ptr());
            if class.is_null() {
                return -1;
            }
            slot.store(jni!(env, NewGlobalRef, class).cast(), Ordering::SeqCst);
        }
        JNI_VERSION_1_8
    }
}

/// The calling thread's environment: a thread native code started is attached as a daemon.
pub fn env() -> *mut JNIEnv {
    let vm = VM.load(Ordering::SeqCst);
    if vm.is_null() {
        return null_mut();
    }
    let mut env: *mut JNIEnv = null_mut();
    // SAFETY: the VM JNI_OnLoad recorded, alive while the library is loaded.
    unsafe {
        let got = ((**vm).GetEnv.expect("GetEnv"))(vm, (&mut env as *mut *mut JNIEnv).cast(), JNI_VERSION_1_8);
        if got == -2 {
            let attach = (**vm).AttachCurrentThreadAsDaemon.expect("AttachCurrentThreadAsDaemon");
            if attach(vm, (&mut env as *mut *mut JNIEnv).cast(), null_mut()) != 0 {
                return null_mut();
            }
        } else if got != 0 {
            return null_mut();
        }
    }
    env
}

/// Frees what the bridge malloc'd (a string or an array out), or null.
pub fn free(memory: *mut c_void) {
    // SAFETY: memory the bridge malloc'd (ccm_free's contract), or null.
    unsafe { c_free(memory) }
}

// ------------------------------------------------------------------ strings

/// A UTF-8 string in, NUL-terminated; null for null.
pub struct CStrBuf(Option<CString>);

impl CStrBuf {
    pub fn as_ptr(&self) -> *const c_char {
        self.0.as_ref().map_or(std::ptr::null(), |s| s.as_ptr())
    }
}

pub unsafe fn utf8(env: *mut JNIEnv, bytes: jbyteArray) -> CStrBuf {
    if bytes.is_null() {
        return CStrBuf(None);
    }
    unsafe {
        let n = jni!(env, GetArrayLength, bytes);
        let mut data = vec![0u8; n as usize];
        jni!(env, GetByteArrayRegion, bytes, 0, n, data.as_mut_ptr().cast());
        data.retain(|b| *b != 0);
        CStrBuf(Some(CString::new(data).unwrap_or_default()))
    }
}

pub unsafe fn bytes(env: *mut JNIEnv, text: *const c_char) -> jobject {
    if text.is_null() {
        return null_mut();
    }
    unsafe {
        let s = CStr::from_ptr(text).to_bytes();
        let array = jni!(env, NewByteArray, s.len() as jsize);
        if !array.is_null() {
            jni!(env, SetByteArrayRegion, array, 0, s.len() as jsize, s.as_ptr().cast());
        }
        array
    }
}

/// out[0] = the string's bytes (or null); the string, malloc'd by the export, freed.
pub unsafe fn put_string(env: *mut JNIEnv, out: jobjectArray, text: *mut c_char) {
    unsafe {
        let b = bytes(env, text);
        jni!(env, SetObjectArrayElement, out, 0, b);
        if !b.is_null() {
            jni!(env, DeleteLocalRef, b);
        }
        c_free(text.cast());
    }
}

pub unsafe fn string_slot(env: *mut JNIEnv) -> jobject {
    unsafe { jni!(env, NewObjectArray, 1, BYTE_ARRAY.load(Ordering::SeqCst).cast(), null_mut()) }
}

/// A malloc'd copy of slot[0], which the call into Java set; null for null.
pub unsafe fn take_string(env: *mut JNIEnv, slot: jobjectArray) -> *mut c_char {
    unsafe {
        let b = jni!(env, GetObjectArrayElement, slot, 0);
        let s = utf8(env, b);
        if !b.is_null() {
            jni!(env, DeleteLocalRef, b);
        }
        match s.0 {
            None => null_mut(),
            Some(c) => {
                let bytes = c.as_bytes_with_nul();
                let copy = malloc(bytes.len()).cast::<u8>();
                if !copy.is_null() {
                    std::ptr::copy_nonoverlapping(bytes.as_ptr(), copy, bytes.len());
                }
                copy.cast()
            }
        }
    }
}

/// Strings in: their pointers and count (-1 for null).
pub struct Strings {
    _owned: Vec<CStrBuf>,
    pointers: Vec<*const c_char>,
    count: i32,
}

impl Strings {
    pub fn as_ptr(&self) -> *const *const c_char {
        if self.count < 0 { std::ptr::null() } else { self.pointers.as_ptr() }
    }
    pub fn count(&self) -> i32 {
        self.count
    }
}

pub unsafe fn strings(env: *mut JNIEnv, array: jobjectArray) -> Strings {
    if array.is_null() {
        return Strings { _owned: Vec::new(), pointers: Vec::new(), count: -1 };
    }
    unsafe {
        let n = jni!(env, GetArrayLength, array);
        let mut owned = Vec::with_capacity(n as usize);
        for i in 0..n {
            let b = jni!(env, GetObjectArrayElement, array, i);
            owned.push(utf8(env, b));
            if !b.is_null() {
                jni!(env, DeleteLocalRef, b);
            }
        }
        let pointers = owned.iter().map(CStrBuf::as_ptr).collect();
        Strings { _owned: owned, pointers, count: n }
    }
}

// ------------------------------------------------------------------ numbers

/// The elements of a Java array of numbers, copied; null for null.
pub struct Elements<T>(Option<Vec<T>>);

impl<T> Elements<T> {
    pub fn as_ptr_or_null(&self) -> *const T {
        self.0.as_ref().map_or(std::ptr::null(), |v| v.as_ptr())
    }
}

macro_rules! numbers {
    ($t:ty, $get:ident, $set:ident, $new:ident, $elements:ident, $release:ident, $put:ident,
     $put_array:ident, $new_array:ident, $read:ident) => {
        pub unsafe fn $elements(env: *mut JNIEnv, array: jarray) -> (Elements<$t>, i32) {
            if array.is_null() {
                return (Elements(None), -1);
            }
            unsafe {
                let n = jni!(env, GetArrayLength, array);
                let mut v: Vec<$t> = vec![Default::default(); n as usize];
                jni!(env, $get, array, 0, n, v.as_mut_ptr().cast());
                (Elements(Some(v)), n)
            }
        }
        pub unsafe fn $release(_env: *mut JNIEnv, _array: jarray, _elements: Elements<$t>) {}
        /// array[0] = value.
        pub unsafe fn $put(env: *mut JNIEnv, array: jarray, value: $t) {
            unsafe { jni!(env, $set, array, 0, 1, (&value as *const $t).cast()) }
        }
        /// out[0] = a new Java array of the export's malloc'd copy.
        pub unsafe fn $put_array(env: *mut JNIEnv, out: jobjectArray, data: *mut $t, count: i32) {
            unsafe {
                let a = jni!(env, $new, count.max(0));
                if !a.is_null() {
                    if count > 0 {
                        jni!(env, $set, a, 0, count, data.cast_const().cast());
                    }
                    jni!(env, SetObjectArrayElement, out, 0, a);
                    jni!(env, DeleteLocalRef, a);
                }
            }
        }
        pub unsafe fn $new_array(env: *mut JNIEnv, n: i32) -> jobject {
            unsafe { jni!(env, $new, n) }
        }
        pub unsafe fn $read(env: *mut JNIEnv, array: jarray) -> $t {
            let mut value: $t = Default::default();
            unsafe { jni!(env, $get, array, 0, 1, (&mut value as *mut $t).cast()) };
            value
        }
    };
}

numbers!(i32, GetIntArrayRegion, SetIntArrayRegion, NewIntArray, int_elements,
         release_int_elements, put_int, put_int_array, new_int_array, get_int);
numbers!(i64, GetLongArrayRegion, SetLongArrayRegion, NewLongArray, long_elements,
         release_long_elements, put_long, put_long_array, new_long_array, get_long);
numbers!(i16, GetShortArrayRegion, SetShortArrayRegion, NewShortArray, short_elements,
         release_short_elements, put_short, put_short_array, new_short_array, get_short);
numbers!(i8, GetByteArrayRegion, SetByteArrayRegion, NewByteArray, byte_elements,
         release_byte_elements, put_byte, put_byte_array, new_byte_array, get_byte);
numbers!(f64, GetDoubleArrayRegion, SetDoubleArrayRegion, NewDoubleArray, double_elements,
         release_double_elements, put_double, put_double_array, new_double_array, get_double);
numbers!(f32, GetFloatArrayRegion, SetFloatArrayRegion, NewFloatArray, float_elements,
         release_float_elements, put_float, put_float_array, new_float_array, get_float);

pub unsafe fn put_boolean(env: *mut JNIEnv, array: jarray, value: bool) {
    let v: jboolean = value as jboolean;
    unsafe { jni!(env, SetBooleanArrayRegion, array, 0, 1, &v) }
}

pub unsafe fn new_boolean_array(env: *mut JNIEnv, n: i32) -> jobject {
    unsafe { jni!(env, NewBooleanArray, n) }
}

pub unsafe fn get_boolean(env: *mut JNIEnv, array: jarray) -> bool {
    let mut v: jboolean = 0;
    unsafe { jni!(env, GetBooleanArrayRegion, array, 0, 1, &mut v) };
    v != 0
}

/// A Java boolean[]'s elements as Rust bools (a jboolean is a byte, any non-zero true); null
/// for null.
pub unsafe fn boolean_elements(env: *mut JNIEnv, array: jarray) -> (Elements<bool>, i32) {
    if array.is_null() {
        return (Elements(None), -1);
    }
    unsafe {
        let n = jni!(env, GetArrayLength, array);
        let mut v: Vec<jboolean> = vec![0; n as usize];
        jni!(env, GetBooleanArrayRegion, array, 0, n, v.as_mut_ptr());
        (Elements(Some(v.into_iter().map(|b| b != 0).collect())), n)
    }
}

/// out[0] = a new Java boolean[] of the export's malloc'd bools.
pub unsafe fn put_boolean_array(env: *mut JNIEnv, out: jobjectArray, data: *mut bool, count: i32) {
    unsafe {
        let a = jni!(env, NewBooleanArray, count.max(0));
        if !a.is_null() {
            if count > 0 {
                let v: Vec<jboolean> =
                    (0..count as usize).map(|i| *data.add(i) as jboolean).collect();
                jni!(env, SetBooleanArrayRegion, a, 0, count, v.as_ptr());
            }
            jni!(env, SetObjectArrayElement, out, 0, a);
            jni!(env, DeleteLocalRef, a);
        }
    }
}

// ------------------------------------------------------------------ references and calls

pub unsafe fn delete_local(env: *mut JNIEnv, object: jobject) {
    if !object.is_null() {
        unsafe { jni!(env, DeleteLocalRef, object) }
    }
}

/// A Java object as a context native code keeps: a global reference.
pub unsafe fn global(env: *mut JNIEnv, object: jobject) -> *mut c_void {
    if object.is_null() {
        return null_mut();
    }
    unsafe { jni!(env, NewGlobalRef, object).cast() }
}

/// The ccm_release_fn of such a context; with no release in the export, the glue calls it
/// after the call.
pub unsafe extern "C" fn release(context: *mut c_void) {
    if context.is_null() {
        return;
    }
    let env = env();
    if !env.is_null() {
        unsafe { jni!(env, DeleteGlobalRef, context.cast()) }
    }
}

/// A static method of CcmNative a trampoline calls, looked up once.
pub struct Method {
    name: &'static CStr,
    signature: &'static CStr,
    id: AtomicPtr<c_void>,
}

impl Method {
    pub const fn new(name: &'static CStr, signature: &'static CStr) -> Self {
        Method { name, signature, id: AtomicPtr::new(null_mut()) }
    }

    fn class(&self) -> jclass {
        NATIVE.load(Ordering::SeqCst).cast()
    }

    /// Calls it; a Java exception left pending (an Error the generated method didn't catch)
    /// goes to CcmInterop.managed and the status is CCM_MANAGED.
    pub unsafe fn call_int(&self, env: *mut JNIEnv, args: &[jvalue]) -> i32 {
        unsafe {
            let mut id = self.id.load(Ordering::SeqCst);
            if id.is_null() {
                id = jni!(env, GetStaticMethodID, self.class(), self.name.as_ptr(), self.signature.as_ptr()).cast();
                if id.is_null() {
                    return pending(env);
                }
                self.id.store(id, Ordering::SeqCst);
            }
            let status = jni!(env, CallStaticIntMethodA, self.class(), id.cast(), args.as_ptr());
            if jni!(env, ExceptionCheck) != 0 {
                return pending(env);
            }
            status
        }
    }
}

/// A Java exception left pending (an Error the generated method didn't catch) goes to
/// CcmInterop.managed, which the façade rethrows: CCM_MANAGED.
pub unsafe fn pending(env: *mut JNIEnv) -> i32 {
    unsafe {
        let thrown = jni!(env, ExceptionOccurred);
        if thrown.is_null() {
            return CCM_ERROR;
        }
        jni!(env, ExceptionClear);
        let class: jclass = INTEROP.load(Ordering::SeqCst).cast();
        let mut id = MANAGED.id.load(Ordering::SeqCst);
        if id.is_null() {
            id = jni!(env, GetStaticMethodID, class, MANAGED.name.as_ptr(), MANAGED.signature.as_ptr()).cast();
            MANAGED.id.store(id, Ordering::SeqCst);
        }
        if !id.is_null() {
            jni!(env, CallStaticIntMethodA, class, id.cast(), [jvalue { l: thrown }].as_ptr());
        }
        if jni!(env, ExceptionCheck) != 0 {
            jni!(env, ExceptionClear);
        }
        jni!(env, DeleteLocalRef, thrown);
        CCM_MANAGED
    }
}

// ------------------------------------------------------------------ CcmInterop's own natives

unsafe extern "C" {
    fn ccm_error_part(part: i32) -> *const c_char;
}

#[unsafe(no_mangle)]
pub unsafe extern "system" fn Java_codecoremorph_interop_CcmInterop_errorPart(
    env: *mut JNIEnv,
    _class: jclass,
    part: jint,
) -> jobject {
    unsafe { bytes(env, ccm_error_part(part)) }
}
