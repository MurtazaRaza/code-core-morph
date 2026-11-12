//! .NET's `System.Decimal` for Rust ports of C# code. Written by CodeCoreMorph; do not edit.
//!
//! A value is a 96-bit unsigned integer, a sign and a scale from 0 to 28, laid out as
//! `decimal.GetBits` lays it out (lo, mid, hi, flags with the sign in bit 31 and the scale in
//! bits 16 to 23), so values cross a bridge unchanged. Arithmetic keeps .NET's scales (1.10 +
//! 1.0 is 2.10) and rounds half to even where a result needs more than 96 bits or a scale above
//! 28, bit for bit as .NET does, zeros' signs and scales included. Where .NET throws, the
//! `checked_*` methods return the [`Exception`] it throws, and the operators panic with it (as
//! Rust's integer operators panic), which a C# port's bridge turns back into that exception.

use std::cmp::Ordering;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::{Add, AddAssign, Div, DivAssign, Mul, MulAssign, Neg, Rem, RemAssign, Sub, SubAssign};
use std::str::FromStr;

use crate::exceptions::{Exception, Result};

const N: usize = 10;

/// A little-endian unsigned integer of 32-bit words, wide enough for every intermediate result.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct Big([u32; N]);

impl Big {
    fn of(lo: u32, mid: u32, hi: u32) -> Big {
        let mut b = Big::default();
        b.0[0] = lo;
        b.0[1] = mid;
        b.0[2] = hi;
        b
    }

    fn small(v: u64) -> Big {
        let mut b = Big::default();
        b.0[0] = v as u32;
        b.0[1] = (v >> 32) as u32;
        b
    }

    fn is_zero(&self) -> bool {
        self.0.iter().all(|&x| x == 0)
    }

    fn fits96(&self) -> bool {
        self.0[3..].iter().all(|&x| x == 0)
    }

    fn odd(&self) -> bool {
        self.0[0] & 1 != 0
    }
}

fn cmp(a: &Big, b: &Big) -> Ordering {
    for i in (0..N).rev() {
        if a.0[i] != b.0[i] {
            return a.0[i].cmp(&b.0[i]);
        }
    }
    Ordering::Equal
}

fn add(a: &Big, b: &Big) -> Big {
    let mut r = Big::default();
    let mut carry = 0u64;
    for i in 0..N {
        let s = u64::from(a.0[i]) + u64::from(b.0[i]) + carry;
        r.0[i] = s as u32;
        carry = s >> 32;
    }
    r
}

/// a - b, for a >= b.
fn sub(a: &Big, b: &Big) -> Big {
    let mut r = Big::default();
    let mut borrow = 0i64;
    for i in 0..N {
        let d = i64::from(a.0[i]) - i64::from(b.0[i]) - borrow;
        borrow = i64::from(d < 0);
        r.0[i] = (d + (borrow << 32)) as u32;
    }
    r
}

fn mul_small(a: &Big, m: u32) -> Big {
    let mut r = Big::default();
    let mut carry = 0u64;
    for i in 0..N {
        let p = u64::from(a.0[i]) * u64::from(m) + carry;
        r.0[i] = p as u32;
        carry = p >> 32;
    }
    r
}

fn mul(a: &Big, b: &Big) -> Big {
    let mut r = Big::default();
    for i in 0..N {
        if a.0[i] == 0 {
            continue;
        }
        let mut carry = 0u64;
        for j in 0..N - i {
            let cur = u64::from(r.0[i + j]) + u64::from(a.0[i]) * u64::from(b.0[j]) + carry;
            r.0[i + j] = cur as u32;
            carry = cur >> 32;
        }
    }
    r
}

fn pow10(mut k: u32) -> Big {
    const SMALL: [u32; 9] = [1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000];
    let mut r = Big::small(1);
    while k >= 9 {
        r = mul_small(&r, 1_000_000_000);
        k -= 9;
    }
    mul_small(&r, SMALL[k as usize])
}

fn shl1(a: &Big) -> Big {
    let mut r = Big::default();
    let mut carry = 0u32;
    for i in 0..N {
        r.0[i] = (a.0[i] << 1) | carry;
        carry = a.0[i] >> 31;
    }
    r
}

/// Long division: a = q * d + r, for d != 0.
fn divmod(a: &Big, d: &Big) -> (Big, Big) {
    let mut q = Big::default();
    let mut r = Big::default();
    for bit in (0..N * 32).rev() {
        r = shl1(&r);
        r.0[0] |= (a.0[bit / 32] >> (bit % 32)) & 1;
        if cmp(&r, d) != Ordering::Less {
            r = sub(&r, d);
            q.0[bit / 32] |= 1 << (bit % 32);
        }
    }
    (q, r)
}

/// q rounded half to even, given the remainder r of a division by d.
fn round_half_even(q: &Big, r: &Big, d: &Big) -> Big {
    let c = cmp(&shl1(r), d);
    if c == Ordering::Greater || (c == Ordering::Equal && q.odd()) {
        add(q, &Big::small(1))
    } else {
        *q
    }
}

const SIGN_BIT: u32 = 0x8000_0000;
const SCALE_MASK: u32 = 0x00FF_0000;

/// .NET's decimal, bit for bit.
#[derive(Clone, Copy, Default)]
pub struct Decimal {
    lo: u32,
    mid: u32,
    hi: u32,
    flags: u32,
}

fn overflow() -> Exception {
    Exception::overflow("Value was either too large or too small for a Decimal.")
}

fn or_panic(result: Result<Decimal>) -> Decimal {
    result.unwrap_or_else(|e| std::panic::panic_any(e))
}

impl Decimal {
    /// The largest scale, 28.
    pub const MAX_SCALE: u32 = 28;
    /// 0, at scale 0.
    pub const ZERO: Decimal = Decimal { lo: 0, mid: 0, hi: 0, flags: 0 };
    /// 1, at scale 0.
    pub const ONE: Decimal = Decimal { lo: 1, mid: 0, hi: 0, flags: 0 };

    /// `mantissa` × 10^-`scale`, at that scale: `Decimal::new(250, 2)` is C#'s `2.50m`. Panics
    /// with .NET's exception for a scale above 28.
    pub fn new(mantissa: i64, scale: u32) -> Decimal {
        if scale > Decimal::MAX_SCALE {
            std::panic::panic_any(Exception::argument_out_of_range(
                "scale",
                "Decimal's scale value must be between 0 and 28, inclusive.",
            ));
        }
        let m = mantissa.unsigned_abs();
        Decimal {
            lo: m as u32,
            mid: (m >> 32) as u32,
            hi: 0,
            flags: (scale << 16) | if mantissa < 0 { SIGN_BIT } else { 0 },
        }
    }

    /// The value of `decimal.GetBits`: lo, mid, hi and flags.
    pub fn from_bits(lo: i32, mid: i32, hi: i32, flags: i32) -> Result<Decimal> {
        let d = Decimal {
            lo: lo as u32,
            mid: mid as u32,
            hi: hi as u32,
            flags: (flags as u32) & (SIGN_BIT | SCALE_MASK),
        };
        if d.scale() > Decimal::MAX_SCALE {
            return Err(Exception::argument("a decimal's scale is at most 28", "bits"));
        }
        Ok(d)
    }

    /// `decimal.GetBits`: lo, mid, hi and flags.
    pub fn bits(&self) -> [i32; 4] {
        [self.lo as i32, self.mid as i32, self.hi as i32, self.flags as i32]
    }

    /// A value written as C# writes a literal (`-12.50`), at the scale written.
    pub fn parse(text: &str) -> Result<Decimal> {
        let bad = || Exception::format(format!("The input string '{text}' was not in a correct format."));
        let bytes = text.as_bytes();
        let mut i = 0;
        let mut neg = false;
        if i < bytes.len() && (bytes[i] == b'-' || bytes[i] == b'+') {
            neg = bytes[i] == b'-';
            i += 1;
        }
        let mut m = Big::default();
        let mut scale: i32 = -1;
        let mut any = false;
        while i < bytes.len() {
            let c = bytes[i];
            i += 1;
            if c == b'.' && scale < 0 {
                scale = 0;
                continue;
            }
            if !c.is_ascii_digit() {
                return Err(bad());
            }
            m = add(&mul_small(&m, 10), &Big::small(u64::from(c - b'0')));
            if scale >= 0 {
                scale += 1;
            }
            any = true;
        }
        if !any {
            return Err(bad());
        }
        Decimal::fit(&m, scale.max(0) as u32, neg)
    }

    /// The scale: digits after the point, 0 to 28.
    pub fn scale(&self) -> u32 {
        (self.flags & SCALE_MASK) >> 16
    }

    pub fn is_negative(&self) -> bool {
        self.flags & SIGN_BIT != 0
    }

    pub fn is_zero(&self) -> bool {
        self.lo == 0 && self.mid == 0 && self.hi == 0
    }

    fn mant(&self) -> Big {
        Big::of(self.lo, self.mid, self.hi)
    }

    fn make(m: &Big, scale: u32, negative: bool) -> Decimal {
        Decimal {
            lo: m.0[0],
            mid: m.0[1],
            hi: m.0[2],
            flags: (scale << 16) | if negative { SIGN_BIT } else { 0 },
        }
    }

    /// An exact mantissa and scale made to fit: the fewest digits dropped, rounded half to even.
    fn fit(m: &Big, scale: u32, negative: bool) -> Result<Decimal> {
        let mut k = scale.saturating_sub(Decimal::MAX_SCALE);
        if k == 0 && m.fits96() {
            return Ok(Decimal::make(m, scale, negative));
        }
        while k <= scale {
            let div = pow10(k);
            let (q, r) = divmod(m, &div);
            let q = round_half_even(&q, &r, &div);
            if q.fits96() {
                return Ok(Decimal::make(&q, scale - k, negative));
            }
            k += 1;
        }
        Err(overflow())
    }

    fn add_signed(a: &Decimal, b: &Decimal, subtract: bool) -> Result<Decimal> {
        let s = a.scale().max(b.scale());
        let ma = mul(&a.mant(), &pow10(s - a.scale()));
        let mb = mul(&b.mant(), &pow10(s - b.scale()));
        let nb = b.is_negative() != subtract;
        if a.is_negative() == nb {
            return Decimal::fit(&add(&ma, &mb), s, a.is_negative());
        }
        match cmp(&ma, &mb) {
            // .NET takes the operand of the larger scale first, and a zero keeps its sign.
            Ordering::Equal => Ok(Decimal::make(
                &Big::default(),
                s,
                if b.scale() > a.scale() { nb } else { a.is_negative() },
            )),
            Ordering::Greater => Decimal::fit(&sub(&ma, &mb), s, a.is_negative()),
            Ordering::Less => Decimal::fit(&sub(&mb, &ma), s, nb),
        }
    }

    pub fn checked_add(self, other: Decimal) -> Result<Decimal> {
        Decimal::add_signed(&self, &other, false)
    }

    pub fn checked_sub(self, other: Decimal) -> Result<Decimal> {
        Decimal::add_signed(&self, &other, true)
    }

    pub fn checked_mul(self, other: Decimal) -> Result<Decimal> {
        let scale = self.scale() + other.scale();
        let neg = self.is_negative() != other.is_negative();
        // .NET's paths: two 32-bit operands keep a zero product's scale and sign, up to a scale
        // past which the product is plain zero; any other zero product is plain zero.
        let small = self.mid == 0 && self.hi == 0 && other.mid == 0 && other.hi == 0;
        if small && scale > Decimal::MAX_SCALE + 19 {
            return Ok(Decimal::ZERO);
        }
        if !small && (self.is_zero() || other.is_zero()) {
            return Ok(Decimal::ZERO);
        }
        Decimal::fit(&mul(&self.mant(), &other.mant()), scale, neg)
    }

    /// The exact quotient at the smallest scale from scale(a) - scale(b) up, else rounded at
    /// the largest scale whose quotient fits, its trailing zeros then dropped (.NET's
    /// unscaling). A quotient that rounds to zero is 0 at scale 0, with its sign.
    pub fn checked_div(self, other: Decimal) -> Result<Decimal> {
        if other.is_zero() {
            return Err(Exception::divide_by_zero());
        }
        let neg = self.is_negative() != other.is_negative();
        let s0 = self.scale() as i32 - other.scale() as i32;
        let natural = s0.max(0);
        let mb = other.mant();
        let mut s = natural;
        while s <= Decimal::MAX_SCALE as i32 {
            let (q, r) = divmod(&mul(&self.mant(), &pow10((s - s0) as u32)), &mb);
            if !q.fits96() {
                break;
            }
            if r.is_zero() {
                return Ok(Decimal::make(&q, s as u32, neg));
            }
            s += 1;
        }
        s -= 1;
        while s >= natural {
            let (q, r) = divmod(&mul(&self.mant(), &pow10((s - s0) as u32)), &mb);
            let mut rounded = round_half_even(&q, &r, &mb);
            if !rounded.fits96() {
                s -= 1;
                continue;
            }
            if rounded.is_zero() {
                return Ok(Decimal::make(&rounded, 0, neg));
            }
            let ten = Big::small(10);
            while s > 0 {
                let (q10, r10) = divmod(&rounded, &ten);
                if !r10.is_zero() {
                    break;
                }
                rounded = q10;
                s -= 1;
            }
            return Ok(Decimal::make(&rounded, s as u32, neg));
        }
        Err(overflow())
    }

    /// `a % b`: what is left of `a` after `truncate(a / b)` times `b`.
    pub fn checked_rem(self, other: Decimal) -> Result<Decimal> {
        let quotient = self.checked_div(other)?.truncate();
        self.checked_sub(quotient.checked_mul(other)?)
    }

    /// `Math.Round(d, decimals)`: half to even.
    pub fn round(self, decimals: i32) -> Result<Decimal> {
        self.round_mode(decimals, false)
    }

    /// `Math.Round(d, decimals, MidpointRounding.AwayFromZero)`.
    pub fn round_away_from_zero(self, decimals: i32) -> Result<Decimal> {
        self.round_mode(decimals, true)
    }

    fn round_mode(self, decimals: i32, away_from_zero: bool) -> Result<Decimal> {
        if !(0..=Decimal::MAX_SCALE as i32).contains(&decimals) {
            return Err(Exception::argument_out_of_range("decimals", ""));
        }
        let decimals = decimals as u32;
        if self.scale() <= decimals {
            return Ok(self);
        }
        let div = pow10(self.scale() - decimals);
        let (mut q, r) = divmod(&self.mant(), &div);
        let c = cmp(&shl1(&r), &div);
        if c == Ordering::Greater || (c == Ordering::Equal && (away_from_zero || q.odd())) {
            q = add(&q, &Big::small(1));
        }
        Ok(Decimal::make(&q, decimals, self.is_negative()))
    }

    /// `d.ToString("F2")` or `d.ToString("0.00")` in the invariant culture, for `decimals`
    /// places: rounded half away from zero, as .NET formats, and padded with zeros.
    pub fn to_fixed(&self, decimals: u32) -> String {
        let rounded = if decimals <= Decimal::MAX_SCALE {
            self.round_mode(decimals as i32, true).unwrap_or(*self)
        } else {
            *self
        };
        let mut text = rounded.to_string();
        let scale = rounded.scale();
        if decimals > scale {
            if scale == 0 {
                text.push('.');
            }
            text.extend(std::iter::repeat_n('0', (decimals - scale) as usize));
        }
        text
    }

    /// `decimal.Truncate`: the integral part, its sign kept.
    pub fn truncate(self) -> Decimal {
        let (q, _) = divmod(&self.mant(), &pow10(self.scale()));
        Decimal::make(&q, 0, self.is_negative())
    }

    /// `(double)d`.
    pub fn to_f64(&self) -> f64 {
        let mut m = f64::from(self.hi) * 18446744073709551616.0
            + f64::from(self.mid) * 4294967296.0
            + f64::from(self.lo);
        for _ in 0..self.scale() {
            m /= 10.0;
        }
        if self.is_negative() { -m } else { m }
    }

    fn compare(&self, other: &Decimal) -> Ordering {
        if self.is_zero() && other.is_zero() {
            return Ordering::Equal;
        }
        if self.is_negative() != other.is_negative() {
            return if self.is_negative() { Ordering::Less } else { Ordering::Greater };
        }
        let s = self.scale().max(other.scale());
        let c = cmp(
            &mul(&self.mant(), &pow10(s - self.scale())),
            &mul(&other.mant(), &pow10(s - other.scale())),
        );
        if self.is_negative() { c.reverse() } else { c }
    }
}

impl PartialEq for Decimal {
    fn eq(&self, other: &Self) -> bool {
        self.compare(other) == Ordering::Equal
    }
}

impl Eq for Decimal {}

impl PartialOrd for Decimal {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Decimal {
    fn cmp(&self, other: &Self) -> Ordering {
        self.compare(other)
    }
}

/// Equal values hash equally, whatever their scales (1.10 and 1.1).
impl Hash for Decimal {
    fn hash<H: Hasher>(&self, state: &mut H) {
        let ten = Big::small(10);
        let mut m = self.mant();
        let mut s = self.scale();
        while s > 0 && !m.is_zero() {
            let (q, r) = divmod(&m, &ten);
            if !r.is_zero() {
                break;
            }
            m = q;
            s -= 1;
        }
        if m.is_zero() {
            0u32.hash(state);
            return;
        }
        (m.0[0], m.0[1], m.0[2], s, self.flags & SIGN_BIT).hash(state);
    }
}

/// `decimal.ToString(CultureInfo.InvariantCulture)`.
impl fmt::Display for Decimal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let ten = Big::small(10);
        let mut digits: Vec<u8> = Vec::new();
        let mut m = self.mant();
        while !m.is_zero() {
            let (q, r) = divmod(&m, &ten);
            digits.push(b'0' + r.0[0] as u8);
            m = q;
        }
        let s = self.scale() as usize;
        while digits.len() <= s {
            digits.push(b'0');
        }
        digits.reverse();
        let mut text = String::with_capacity(digits.len() + 2);
        if self.is_negative() && !self.is_zero() {
            text.push('-');
        }
        let point = digits.len() - s;
        text.push_str(std::str::from_utf8(&digits[..point]).unwrap_or("0"));
        if s > 0 {
            text.push('.');
            text.push_str(std::str::from_utf8(&digits[point..]).unwrap_or(""));
        }
        f.pad(&text)
    }
}

impl fmt::Debug for Decimal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self}m")
    }
}

impl FromStr for Decimal {
    type Err = Exception;

    fn from_str(text: &str) -> Result<Decimal> {
        Decimal::parse(text)
    }
}

macro_rules! from_signed {
    ($($t:ty),*) => {$(
        /// C#'s implicit conversion.
        impl From<$t> for Decimal {
            fn from(v: $t) -> Decimal {
                let v = i64::from(v);
                let m = v.unsigned_abs();
                Decimal {
                    lo: m as u32,
                    mid: (m >> 32) as u32,
                    hi: 0,
                    flags: if v < 0 { SIGN_BIT } else { 0 },
                }
            }
        }
    )*};
}

macro_rules! from_unsigned {
    ($($t:ty),*) => {$(
        /// C#'s implicit conversion.
        impl From<$t> for Decimal {
            fn from(v: $t) -> Decimal {
                let m = u64::from(v);
                Decimal { lo: m as u32, mid: (m >> 32) as u32, hi: 0, flags: 0 }
            }
        }
    )*};
}

from_signed!(i8, i16, i32, i64);
from_unsigned!(u8, u16, u32, u64);

impl Neg for Decimal {
    type Output = Decimal;

    fn neg(self) -> Decimal {
        Decimal { flags: self.flags ^ SIGN_BIT, ..self }
    }
}

macro_rules! operator {
    ($trait:ident, $method:ident, $assign:ident, $assign_method:ident, $checked:ident) => {
        /// Panics with .NET's exception where .NET throws; `checked_*` returns it.
        impl $trait for Decimal {
            type Output = Decimal;

            fn $method(self, other: Decimal) -> Decimal {
                or_panic(self.$checked(other))
            }
        }

        impl $assign for Decimal {
            fn $assign_method(&mut self, other: Decimal) {
                *self = or_panic(self.$checked(other));
            }
        }
    };
}

operator!(Add, add, AddAssign, add_assign, checked_add);
operator!(Sub, sub, SubAssign, sub_assign, checked_sub);
operator!(Mul, mul, MulAssign, mul_assign, checked_mul);
operator!(Div, div, DivAssign, div_assign, checked_div);
operator!(Rem, rem, RemAssign, rem_assign, checked_rem);

impl std::iter::Sum for Decimal {
    fn sum<I: Iterator<Item = Decimal>>(iter: I) -> Decimal {
        iter.fold(Decimal::ZERO, |a, b| a + b)
    }
}
