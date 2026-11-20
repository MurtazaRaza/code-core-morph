//! The bridge of shop.Money: an immutable value, crossing as its fields (cents, currency).
use super::ccm::{self, ccm_status};
use codecoremorph_java::Exception;
use crate::money::Money;
use std::ffi::c_char;

fn money(cents: i64, currency: *const c_char) -> Result<Money, Exception> {
    Money::new(cents, ccm::str_in(currency, Some("currency"))?)
}

fn out(m: &Money, result_cents: *mut i64, result_currency: *mut *mut c_char) {
    ccm::put(result_cents, m.cents());
    ccm::out_str(result_currency, m.currency());
}

/// Checks the fields as the constructor does.
#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_new(cents: i64, currency: *const c_char) -> ccm_status {
    ccm::guard(|| money(cents, currency).map(|_| ()))
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_parse(
    text: *const c_char,
    result_cents: *mut i64,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        out(&Money::parse(&ccm::str_in(text, None)?)?, result_cents, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_plus(
    cents: i64,
    currency: *const c_char,
    other_cents: i64,
    other_currency: *const c_char,
    result_cents: *mut i64,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        out(&money(cents, currency)?.plus(&money(other_cents, other_currency)?)?, result_cents, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_times(cents: i64, currency: *const c_char, n: i32, result_cents: *mut i64) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result_cents, money(cents, currency)?.times(n).cents());
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_percent(
    cents: i64,
    currency: *const c_char,
    percent: i32,
    result_cents: *mut i64,
) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result_cents, money(cents, currency)?.percent(percent).cents());
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_compareTo(
    cents: i64,
    currency: *const c_char,
    other_cents: i64,
    other_currency: *const c_char,
    result: *mut i32,
) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result, money(cents, currency)?.compare_to(&money(other_cents, other_currency)?)?);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_hashCode(cents: i64, currency: *const c_char, result: *mut i32) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result, money(cents, currency)?.hash_code());
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_toString(cents: i64, currency: *const c_char, result: *mut *mut c_char) -> ccm_status {
    ccm::guard(|| {
        ccm::out_str(result, &money(cents, currency)?.to_java_string());
        Ok(())
    })
}
