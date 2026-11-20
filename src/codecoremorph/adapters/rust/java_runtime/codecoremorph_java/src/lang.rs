//! Java's semantics where Rust's differ. Java's `int` and `long` arithmetic wraps (Rust's panics
//! on overflow in a debug build: use `wrapping_*`, or these), its integer division by zero
//! throws `ArithmeticException`, its shifts mask their count, and its strings are UTF-16:
//! `String.hashCode` and lengths count UTF-16 units.
use crate::Exception;

/// `String.hashCode`: s[0]*31^(n-1) + ... + s[n-1] over the UTF-16 units, wrapping.
pub fn string_hash(s: &str) -> i32 {
    s.encode_utf16().fold(0i32, |h, u| h.wrapping_mul(31).wrapping_add(i32::from(u)))
}

/// `String.length`: the UTF-16 units.
pub fn string_length(s: &str) -> i32 {
    s.encode_utf16().count() as i32
}

/// `Long.hashCode(v)`: `(int) (v ^ (v >>> 32))`.
pub fn long_hash(v: i64) -> i32 {
    (v ^ ((v as u64) >> 32) as i64) as i32
}

/// `Boolean.hashCode`.
pub fn bool_hash(v: bool) -> i32 {
    if v {
        1231
    } else {
        1237
    }
}

/// `a / b` as Java computes it for `int`: `ArithmeticException("/ by zero")`, and
/// `MIN_VALUE / -1` is `MIN_VALUE`.
pub fn div_i32(a: i32, b: i32) -> Result<i32, Exception> {
    if b == 0 {
        return Err(Exception::arithmetic("/ by zero"));
    }
    Ok(a.wrapping_div(b))
}

/// `a % b` as Java computes it for `int`.
pub fn rem_i32(a: i32, b: i32) -> Result<i32, Exception> {
    if b == 0 {
        return Err(Exception::arithmetic("/ by zero"));
    }
    Ok(a.wrapping_rem(b))
}

/// `a / b` as Java computes it for `long`.
pub fn div_i64(a: i64, b: i64) -> Result<i64, Exception> {
    if b == 0 {
        return Err(Exception::arithmetic("/ by zero"));
    }
    Ok(a.wrapping_div(b))
}

/// `a % b` as Java computes it for `long`.
pub fn rem_i64(a: i64, b: i64) -> Result<i64, Exception> {
    if b == 0 {
        return Err(Exception::arithmetic("/ by zero"));
    }
    Ok(a.wrapping_rem(b))
}

/// `a << n` for an `int`: the count masked to 5 bits, as Java does.
pub fn shl_i32(a: i32, n: i32) -> i32 {
    a.wrapping_shl(n as u32 & 31)
}

/// `a >> n` for an `int`.
pub fn shr_i32(a: i32, n: i32) -> i32 {
    a.wrapping_shr(n as u32 & 31)
}

/// `a >>> n` for an `int`.
pub fn ushr_i32(a: i32, n: i32) -> i32 {
    ((a as u32) >> (n as u32 & 31)) as i32
}

/// `a << n` for a `long`: the count masked to 6 bits.
pub fn shl_i64(a: i64, n: i32) -> i64 {
    a.wrapping_shl(n as u32 & 63)
}

/// `a >> n` for a `long`.
pub fn shr_i64(a: i64, n: i32) -> i64 {
    a.wrapping_shr(n as u32 & 63)
}

/// `a >>> n` for a `long`.
pub fn ushr_i64(a: i64, n: i32) -> i64 {
    ((a as u64) >> (n as u32 & 63)) as i64
}

/// `(int) d` for a `double`: saturating, NaN is 0, as Java converts.
pub fn double_to_int(d: f64) -> i32 {
    d as i32 // Rust's `as` saturates and maps NaN to 0, as Java does
}

/// `Math.addExact` for `int`.
pub fn add_exact_i32(a: i32, b: i32) -> Result<i32, Exception> {
    a.checked_add(b).ok_or_else(|| Exception::arithmetic("integer overflow"))
}

/// `Math.addExact` for `long`.
pub fn add_exact_i64(a: i64, b: i64) -> Result<i64, Exception> {
    a.checked_add(b).ok_or_else(|| Exception::arithmetic("long overflow"))
}

/// `Math.multiplyExact` for `int`.
pub fn multiply_exact_i32(a: i32, b: i32) -> Result<i32, Exception> {
    a.checked_mul(b).ok_or_else(|| Exception::arithmetic("integer overflow"))
}

/// `Math.multiplyExact` for `long`.
pub fn multiply_exact_i64(a: i64, b: i64) -> Result<i64, Exception> {
    a.checked_mul(b).ok_or_else(|| Exception::arithmetic("long overflow"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hashes_as_java_does() {
        assert_eq!(string_hash("EUR"), 69026);
        assert_eq!(long_hash(i64::MAX), i32::MIN);
        assert_eq!(string_length("\u{1F600}"), 2);
    }

    #[test]
    fn divides_and_shifts_as_java_does() {
        assert_eq!(div_i32(i32::MIN, -1).unwrap(), i32::MIN);
        assert!(div_i32(1, 0).is_err());
        assert_eq!(shl_i32(1, 33), 2);
        assert_eq!(ushr_i32(-1, 28), 15);
        assert_eq!(double_to_int(f64::NAN), 0);
        assert_eq!(double_to_int(1e20), i32::MAX);
    }
}
