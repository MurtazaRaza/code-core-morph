//! The bridge's side of Microsoft.Extensions.Logging (D-95). Written by CodeCoreMorph; do not
//! edit; the retirement deletes it with the bridge.
//!
//! A C# `ILogger` or `ILoggerFactory` crosses as a context (a GCHandle the façades' support
//! makes with `CcmLogging.Pass`, null for null), which the native side releases when it lets
//! go. The support registers its callbacks once (`ccm_logging_init`), before the first context
//! crosses. A log entry crosses with its state (D-97): its template (null for a message logged
//! without one) and each hole's name and value, which the support logs as .NET's state; a
//! logged exception crosses as the status whose error the support reads during the call (the C#
//! exception itself for CCM_MANAGED), CCM_OK for none.
use std::ffi::{c_char, c_void, CString};
use std::sync::{Arc, OnceLock};

use codecoremorph_runtime::logging::{EventId, LogLevel, LogState, Logger, LoggerFactory};
use codecoremorph_runtime::{Exception, Result};

use super::ccm::{self, ccm_release_fn, ccm_status, ManagedRef, CCM_OK};

/// A log entry's named value: its hole's name, and the value's .NET type (null for null) and
/// invariant data.
#[repr(C)]
pub struct ccm_log_value {
    pub name: *const c_char,
    pub r#type: *const c_char,
    pub data: *const c_char,
}

pub type ccm_log_enabled_fn =
    Option<unsafe extern "C" fn(logger: *mut c_void, level: i32, enabled: *mut bool) -> ccm_status>;
pub type ccm_log_fn = Option<
    unsafe extern "C" fn(
        logger: *mut c_void,
        level: i32,
        event_id: i32,
        event_name: *const c_char,
        message: *const c_char,
        message_template: *const c_char,
        values: *const ccm_log_value,
        count: i32,
        exception: ccm_status,
    ) -> ccm_status,
>;
pub type ccm_create_logger_fn = Option<
    unsafe extern "C" fn(
        factory: *mut c_void,
        category: *const c_char,
        logger: *mut *mut c_void,
    ) -> ccm_status,
>;

struct Callbacks {
    enabled: ccm_log_enabled_fn,
    log: ccm_log_fn,
    create: ccm_create_logger_fn,
    release: ccm_release_fn,
}

static CALLBACKS: OnceLock<Callbacks> = OnceLock::new();

fn callbacks() -> Result<&'static Callbacks> {
    CALLBACKS
        .get()
        .ok_or_else(|| Exception::invalid_operation("the façades' logging support isn't set up"))
}

/// Called once by the façades' support with its calls back into C#.
#[unsafe(no_mangle)]
pub extern "C" fn ccm_logging_init(
    enabled: ccm_log_enabled_fn,
    log: ccm_log_fn,
    create: ccm_create_logger_fn,
    release: ccm_release_fn,
) {
    let _ = CALLBACKS.set(Callbacks { enabled, log, create, release });
}

fn c_text(text: &str) -> CString {
    CString::new(text.replace('\0', " ")).unwrap_or_default()
}

struct ManagedLogger(Arc<ManagedRef>);

impl Logger for ManagedLogger {
    fn is_enabled(&self, level: LogLevel) -> Result<bool> {
        let call = ccm::callback(callbacks()?.enabled)?;
        let mut enabled = false;
        // SAFETY: the support's callback, with the context it made for this logger.
        ccm::check_managed(unsafe { call(self.0.context(), level as i32, &mut enabled) })?;
        Ok(enabled)
    }

    // A message logged without a template: .NET's state is the message itself.
    fn log(
        &self,
        level: LogLevel,
        event: &EventId,
        message: &str,
        exception: Option<&Exception>,
    ) -> Result<()> {
        self.send(level, event, message, None, &[], exception)
    }

    fn log_state(
        &self,
        level: LogLevel,
        event: &EventId,
        state: &LogState,
        exception: Option<&Exception>,
    ) -> Result<()> {
        let texts: Vec<[CString; 2]> = state
            .values
            .iter()
            .map(|(name, value)| [c_text(name), c_text(&value.data)])
            .collect();
        let types: Vec<Option<CString>> = state
            .values
            .iter()
            .map(|(_, value)| (!value.is_null()).then(|| c_text(&value.dotnet_type)))
            .collect();
        let values: Vec<ccm_log_value> = texts
            .iter()
            .zip(&types)
            .map(|([name, data], dotnet_type)| ccm_log_value {
                name: name.as_ptr(),
                r#type: dotnet_type.as_ref().map_or(std::ptr::null(), |t| t.as_ptr()),
                data: data.as_ptr(),
            })
            .collect();
        self.send(level, event, &state.message, Some(&state.template), &values, exception)
    }
}

impl ManagedLogger {
    fn send(
        &self,
        level: LogLevel,
        event: &EventId,
        message: &str,
        template: Option<&str>,
        values: &[ccm_log_value],
        exception: Option<&Exception>,
    ) -> Result<()> {
        let call = ccm::callback(callbacks()?.log)?;
        let name = event.name.as_deref().map(c_text);
        let message = c_text(message);
        let template = template.map(c_text);
        let ptr = |text: Option<&CString>| text.map_or(std::ptr::null(), |t| t.as_ptr());
        // The exception's record, which the support reads during the call: a C# exception is
        // the object C# threw.
        let thrown = exception.map_or(CCM_OK, ccm::record);
        let count = i32::try_from(values.len()).unwrap_or(i32::MAX);
        let values = if values.is_empty() { std::ptr::null() } else { values.as_ptr() };
        // SAFETY: as is_enabled; the strings and the values outlive the call.
        ccm::check_managed(unsafe {
            call(
                self.0.context(),
                level as i32,
                event.id,
                ptr(name.as_ref()),
                message.as_ptr(),
                ptr(template.as_ref()),
                values,
                count,
                thrown,
            )
        })
    }
}

struct ManagedLoggerFactory(Arc<ManagedRef>);

impl LoggerFactory for ManagedLoggerFactory {
    fn create_logger(&self, category: &str) -> Result<Arc<dyn Logger>> {
        let call = ccm::callback(callbacks()?.create)?;
        let category = c_text(category);
        let mut made: *mut c_void = std::ptr::null_mut();
        // SAFETY: as ManagedLogger's; `made` receives a new context, which `logger` takes.
        ccm::check_managed(unsafe { call(self.0.context(), category.as_ptr(), &mut made) })?;
        logger(made).ok_or_else(Exception::null_reference)
    }
}

fn managed(context: *mut c_void) -> Option<Arc<ManagedRef>> {
    if context.is_null() {
        return None;
    }
    let release = CALLBACKS.get().and_then(|c| c.release);
    Some(ManagedRef::new(context, release))
}

/// The C# logger a context holds, as the ported code's `Logger`; None for null. The native
/// side owns the context from here on.
pub fn logger(context: *mut c_void) -> Option<Arc<dyn Logger>> {
    managed(context).map(|m| Arc::new(ManagedLogger(m)) as Arc<dyn Logger>)
}

/// The C# factory a context holds, as the ported code's `LoggerFactory`; None for null.
pub fn logger_factory(context: *mut c_void) -> Option<Arc<dyn LoggerFactory>> {
    managed(context).map(|m| Arc::new(ManagedLoggerFactory(m)) as Arc<dyn LoggerFactory>)
}
