
// Exports no façade calls, over the scalars the glue writes no other export of this bridge
// for: they must still build (M14 found the glue naming jshort and jdouble, which it didn't
// import, and zeroing an f64 with an integer literal), and the JVM must load the library.

/// A call into Java taking a byte, a char's UTF-16 unit, a float, a double and a boolean.
pub type shop_Scalars_each_fn = Option<
    unsafe extern "C" fn(
        context: *mut std::ffi::c_void,
        b: i8,
        s: u16,
        f: f32,
        d: f64,
        z: bool,
        result: *mut f64,
    ) -> ccm_status,
>;

#[unsafe(no_mangle)]
pub extern "C" fn shop_Scalars_mix(
    b: i8,
    s: u16,
    f: f32,
    d: f64,
    z: bool,
    result_f: *mut f32,
    result_d: *mut f64,
) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result_f, f + f32::from(b));
        ccm::put(result_d, d + f64::from(s) + if z { 1.0 } else { 0.0 });
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Scalars_flags(
    flags_data: *const bool,
    flags_count: i32,
    result_data: *mut *mut bool,
    result_count: *mut i32,
) -> ccm_status {
    ccm::guard(|| {
        let flags: Vec<bool> = ccm::array(flags_data, flags_count)?.iter().map(|f| !f).collect();
        ccm::out_array(result_data, result_count, &flags);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Scalars_call(
    context: *mut std::ffi::c_void,
    each: shop_Scalars_each_fn,
    result: *mut f64,
) -> ccm_status {
    ccm::guard(|| {
        let each = ccm::callback(each)?;
        let mut out = 0.0;
        // SAFETY: the glue's trampoline, called with the context it passed and a live out.
        ccm::check_managed(unsafe { each(context, 1, 2, 3.0, 4.0, true, &mut out) })?;
        ccm::put(result, out);
        Ok(())
    })
}
