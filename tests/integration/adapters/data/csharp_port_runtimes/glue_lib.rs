//! The bridge's glue (ccm.rs, ccm_logging.rs, ccm_tasks.rs) as a port's crate has it, with a
//! check of the M13 fixes (D-97) through the error record a façade reads.
#![deny(unsafe_code)]

#[allow(unsafe_code)]
mod bridge;

#[cfg(test)]
#[allow(unsafe_code)]
mod tests {
    use std::ffi::{c_void, CStr};
    use std::ptr::NonNull;

    use codecoremorph_runtime::tasks::{delay, CancellationTokenSource};
    use codecoremorph_runtime::{DotnetValue, Exception};

    use crate::bridge::{ccm, ccm_tasks};

    fn part(i: i32) -> String {
        // SAFETY: the record's part, valid until the thread's next export call.
        unsafe { CStr::from_ptr(ccm::ccm_error_part(i)) }.to_string_lossy().into_owned()
    }

    fn token_origin() -> Option<*mut c_void> {
        let mut origin: *mut c_void = NonNull::<c_void>::dangling().as_ptr();
        // SAFETY: a pointer to a local.
        let has = unsafe { ccm::ccm_error_token(&mut origin) };
        has.then_some(origin)
    }

    #[test]
    fn actual_values_cross() {
        let status =
            ccm::guard(|| Err(Exception::argument_out_of_range_value("Attempts", 0, "too few")));
        assert_eq!(status, ccm::CCM_ERROR);
        assert_eq!(part(0), "System.ArgumentOutOfRangeException");
        assert_eq!(part(2), "Attempts");
        assert_eq!(part(4), "System.Int32");
        assert_eq!(part(5), "0");
        let e = Exception::argument_out_of_range_value(
            "Delay",
            DotnetValue::time_span(-10_000_000),
            "Delay must not be negative.",
        );
        let newline = if cfg!(windows) { "\r\n" } else { "\n" };
        assert_eq!(
            e.to_string(),
            format!("Delay must not be negative. (Parameter 'Delay'){newline}Actual value was -00:00:01.")
        );
        assert_eq!(e.actual_value().map(|v| v.data.as_str()), Some("-10000000"));
        assert_ne!(e, Exception::argument_out_of_range("Delay", "Delay must not be negative."));
        ccm::guard(|| Err(Exception::argument_out_of_range("n", "plain")));
        assert_eq!(part(4), "");
        assert_eq!(part(5), "");
        assert_eq!(token_origin(), None);
        assert_eq!(DotnetValue::from(std::time::Duration::from_millis(1500)).data, "15000000");
        assert_eq!(DotnetValue::from(1e9f32).data, "1E+09");
        assert_eq!(DotnetValue::from(Some(3usize)).dotnet_type, "System.UInt64");
        assert!(DotnetValue::from(Option::<i32>::None).is_null());
    }

    #[test]
    fn tokens_cross() {
        unsafe extern "C" fn release(_context: *mut c_void) {}
        let csharp = Box::into_raw(Box::new(42i32)).cast::<c_void>();
        let native = ccm_tasks::ccm_token_new(csharp, Some(release));
        let token = ccm_tasks::token(native);
        ccm_tasks::ccm_token_cancel(native);
        let status = ccm::guard(|| token.throw_if_cancellation_requested());
        assert_eq!(status, ccm::CCM_ERROR);
        assert_eq!(part(0), "System.OperationCanceledException");
        assert_eq!(token_origin(), Some(csharp));
        let canceled = delay(None, &token).wait().unwrap_err();
        assert!(canceled.is("System.Threading.Tasks.TaskCanceledException"));
        assert!(canceled.cancellation_token().same(&token));
        ccm::guard(|| Err(canceled.clone()));
        assert_eq!(token_origin(), Some(csharp));

        let source = CancellationTokenSource::new();
        let waiting = delay(None, &source.token());
        source.cancel();
        let e = waiting.wait().unwrap_err();
        assert!(waiting.is_canceled());
        assert!(e.cancellation_token().same(&source.token()));
        ccm::guard(|| Err(e.clone()));
        assert_eq!(token_origin(), Some(std::ptr::null_mut()));
        assert!(Exception::operation_canceled().token_origin().is_none());
        assert!(!Exception::operation_canceled().cancellation_token().can_be_canceled());
        ccm_tasks::ccm_token_release(native);
        // SAFETY: the box made above, which the token's release left alone.
        drop(unsafe { Box::from_raw(csharp.cast::<i32>()) });
    }
}
