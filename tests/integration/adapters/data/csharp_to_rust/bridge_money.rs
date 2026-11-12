//! The bridge of Shop.Money, a value type: it crosses as its amount and its currency (null for
//! `default(Money)`).
use std::ffi::c_char;

use super::ccm::{self, ccm_decimal, ccm_status};
use crate::money::Money;
use codecoremorph_runtime::Result;

/// A Money from the façade's fields: a null currency is `default(Money)`.
pub(crate) fn money(amount: ccm_decimal, currency: *const c_char) -> Result<Money> {
    match ccm::opt_str(currency) {
        None => Ok(Money::default()),
        Some(code) => Money::new(ccm::dec(amount)?, Some(&code)),
    }
}

pub(crate) fn out_money(m: &Money, amount: *mut ccm_decimal, currency: *mut *mut c_char) {
    ccm::put(amount, ccm::dec_out(m.amount()));
    ccm::out_opt_str(currency, m.currency());
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_new(
    amount: ccm_decimal,
    currency: *const c_char,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        let m = Money::new(ccm::dec(amount)?, ccm::opt_str(currency).as_deref())?;
        out_money(&m, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_zero(
    currency: *const c_char,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        out_money(&Money::zero(ccm::opt_str(currency).as_deref())?, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_add(
    amount: ccm_decimal,
    currency: *const c_char,
    other_amount: ccm_decimal,
    other_currency: *const c_char,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        let sum = money(amount, currency)?.add(&money(other_amount, other_currency)?)?;
        out_money(&sum, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_subtract(
    amount: ccm_decimal,
    currency: *const c_char,
    other_amount: ccm_decimal,
    other_currency: *const c_char,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        let difference = money(amount, currency)?.subtract(&money(other_amount, other_currency)?)?;
        out_money(&difference, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_times(
    amount: ccm_decimal,
    currency: *const c_char,
    quantity: i32,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        out_money(&money(amount, currency)?.times(quantity)?, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_percent(
    amount: ccm_decimal,
    currency: *const c_char,
    percent: i32,
    result_amount: *mut ccm_decimal,
    result_currency: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        out_money(&money(amount, currency)?.percent(percent)?, result_amount, result_currency);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_equals(
    amount: ccm_decimal,
    currency: *const c_char,
    other_amount: ccm_decimal,
    other_currency: *const c_char,
    result: *mut bool,
) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result, money(amount, currency)? == money(other_amount, other_currency)?);
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_hash(
    amount: ccm_decimal,
    currency: *const c_char,
    result: *mut i32,
) -> ccm_status {
    ccm::guard(|| {
        ccm::put(result, money(amount, currency)?.hash_code());
        Ok(())
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn shop_Money_to_string(
    amount: ccm_decimal,
    currency: *const c_char,
    result: *mut *mut c_char,
) -> ccm_status {
    ccm::guard(|| {
        ccm::out_str(result, &money(amount, currency)?.to_string());
        Ok(())
    })
}
