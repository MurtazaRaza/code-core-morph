//! Errors that mirror .NET's exceptions, for Rust ports of C# code. Written by CodeCoreMorph;
//! do not edit.
//!
//! An [`Exception`] names the .NET type it stands for, so while a port keeps C# façades the
//! bridge hands the façade what to throw; after the port it is the crate's error type. A
//! project's own exception type is [`Exception::custom`], with its .NET type and the arguments
//! its constructor takes, by name. An empty message is the one .NET gives the type.
//!
//! A [`DotnetValue`] is a value as .NET has it (D-97): an ArgumentOutOfRangeException's actual
//! value ([`Exception::argument_out_of_range_value`]), a log entry's named values. It converts
//! from a number, a bool, a string, an `Option` or a `std::time::Duration` (a TimeSpan); a
//! project's own type (its TimeSpan, an enum) implements `From<T> for DotnetValue`.

use std::any::Any;
use std::borrow::Cow;
use std::error::Error;
use std::fmt;
use std::sync::Arc;

/// What a ported member returns where the C# could throw.
pub type Result<T, E = Exception> = std::result::Result<T, E>;

/// What a C# exception passing through native code is to the bridge: its handle, opaque here.
pub type Origin = Arc<dyn Any + Send + Sync>;

/// A .NET value as it crosses to C# (D-97): its type's full name (`"System.Int32"`; empty for
/// null) and its invariant data (`"5"`; a TimeSpan's ticks), which the façades' support reads
/// back into that type, and its text as .NET's `ToString` writes it, where that isn't the data
/// (`"00:00:01.5000000"` for a TimeSpan, an enum's name).
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct DotnetValue {
    pub dotnet_type: String,
    pub data: String,
    pub text: String,
}

impl DotnetValue {
    /// C#'s null.
    pub fn null() -> DotnetValue {
        DotnetValue::default()
    }

    /// A value of the .NET type `dotnet_type`, whose text is its data.
    pub fn new(dotnet_type: impl Into<String>, data: impl Into<String>) -> DotnetValue {
        DotnetValue { dotnet_type: dotnet_type.into(), data: data.into(), text: String::new() }
    }

    /// A value whose text isn't its data (an enum: its number and its name).
    pub fn with_text(
        dotnet_type: impl Into<String>,
        data: impl Into<String>,
        text: impl Into<String>,
    ) -> DotnetValue {
        DotnetValue { dotnet_type: dotnet_type.into(), data: data.into(), text: text.into() }
    }

    /// A System.TimeSpan of `ticks` (100 ns).
    pub fn time_span(ticks: i64) -> DotnetValue {
        DotnetValue::with_text("System.TimeSpan", ticks.to_string(), time_span_text(ticks))
    }

    pub fn is_null(&self) -> bool {
        self.dotnet_type.is_empty()
    }

    /// What .NET's `ToString` gives.
    pub fn shown(&self) -> &str {
        if self.text.is_empty() { &self.data } else { &self.text }
    }
}

/// A TimeSpan's ticks (100 ns) as its `ToString` writes them: `[-][d.]hh:mm:ss[.fffffff]`.
pub fn time_span_text(ticks: i64) -> String {
    let sign = if ticks < 0 { "-" } else { "" };
    let mut left = ticks.unsigned_abs();
    let fraction = left % 10_000_000;
    left /= 10_000_000;
    let (seconds, minutes, hours, days) = (left % 60, left / 60 % 60, left / 3600 % 24, left / 86_400);
    let day = if days == 0 { String::new() } else { format!("{days}.") };
    let fraction = if fraction == 0 { String::new() } else { format!(".{fraction:07}") };
    format!("{sign}{day}{hours:02}:{minutes:02}:{seconds:02}{fraction}")
}

/// The shortest text that reads back as the same number, as .NET Core writes it (`1.5`,
/// `1E+20`, `1E-05`, `NaN`, `Infinity`): `shortest` is Rust's `{:e}` of the number,
/// `scientific_from` its round-trip precision (9 for a float, 17 for a double).
pub(crate) fn floating_text(value: f64, shortest: &str, scientific_from: i32) -> String {
    if value.is_nan() {
        return "NaN".to_string();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0" } else { "0" }.to_string();
    }
    // As many digits as the shortest text that reads back takes, and of those the nearest the
    // value, a tie to the even digit, as .NET picks (Rust's exact formatting rounds so).
    let count = shortest.split('e').next().unwrap_or("").bytes().filter(u8::is_ascii_digit).count();
    let exact = format!("{value:.*e}", count.saturating_sub(1));
    let (mantissa, exponent) = exact.split_once('e').unwrap_or((&exact, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let negative = mantissa.starts_with('-');
    let mut digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    while digits.len() > 1 && digits.ends_with('0') {
        digits.pop();
    }
    let sign = if negative { "-" } else { "" };
    // .NET writes it in scientific notation from its round-trip precision, and below E-04.
    if !(-4..scientific_from).contains(&exponent) {
        let (first, rest) = digits.split_at(1);
        let point = if rest.is_empty() { String::new() } else { format!(".{rest}") };
        let power = if exponent < 0 { '-' } else { '+' };
        return format!("{sign}{first}{point}E{power}{:02}", exponent.abs());
    }
    if exponent < 0 {
        let zeros = "0".repeat(usize::try_from(-exponent - 1).unwrap_or(0));
        return format!("{sign}0.{zeros}{digits}");
    }
    let whole = usize::try_from(exponent).unwrap_or(0) + 1;
    if digits.len() <= whole {
        return format!("{sign}{digits}{}", "0".repeat(whole - digits.len()));
    }
    let (int, frac) = digits.split_at(whole);
    format!("{sign}{int}.{frac}")
}

impl From<f64> for DotnetValue {
    fn from(value: f64) -> DotnetValue {
        DotnetValue::new("System.Double", floating_text(value, &format!("{value:e}"), 17))
    }
}

impl From<f32> for DotnetValue {
    fn from(value: f32) -> DotnetValue {
        DotnetValue::new("System.Single", floating_text(f64::from(value), &format!("{value:e}"), 9))
    }
}

impl From<bool> for DotnetValue {
    fn from(value: bool) -> DotnetValue {
        DotnetValue::new("System.Boolean", if value { "True" } else { "False" })
    }
}

impl From<char> for DotnetValue {
    fn from(value: char) -> DotnetValue {
        DotnetValue::new("System.Char", value.to_string())
    }
}

impl From<&str> for DotnetValue {
    fn from(value: &str) -> DotnetValue {
        DotnetValue::new("System.String", value)
    }
}

impl From<String> for DotnetValue {
    fn from(value: String) -> DotnetValue {
        DotnetValue::new("System.String", value)
    }
}

impl From<&String> for DotnetValue {
    fn from(value: &String) -> DotnetValue {
        DotnetValue::new("System.String", value.as_str())
    }
}

/// A duration is a TimeSpan: its whole ticks.
impl From<std::time::Duration> for DotnetValue {
    fn from(value: std::time::Duration) -> DotnetValue {
        DotnetValue::time_span(i64::try_from(value.as_nanos() / 100).unwrap_or(i64::MAX))
    }
}

impl<T: Into<DotnetValue>> From<Option<T>> for DotnetValue {
    fn from(value: Option<T>) -> DotnetValue {
        value.map_or_else(DotnetValue::null, Into::into)
    }
}

macro_rules! integer_values {
    ($($t:ty => $name:literal),*) => {
        $(impl From<$t> for DotnetValue {
            fn from(value: $t) -> DotnetValue {
                DotnetValue::new($name, value.to_string())
            }
        })*
    };
}

// An integer as the .NET type of its width: i32 is System.Int32, usize System.UInt64.
integer_values!(
    i8 => "System.SByte", u8 => "System.Byte", i16 => "System.Int16", u16 => "System.UInt16",
    i32 => "System.Int32", u32 => "System.UInt32", i64 => "System.Int64", u64 => "System.UInt64",
    isize => "System.Int64", usize => "System.UInt64"
);

/// What an OperationCanceledException keeps of its token (D-97): the tasks module's token,
/// opaque here, and the C# token it stands for (the bridge's), if any.
#[derive(Clone)]
pub(crate) struct CanceledBy {
    // Read by the tasks module, which a port without async code doesn't have.
    #[allow(dead_code)]
    pub(crate) token: Arc<dyn Any + Send + Sync>,
    pub(crate) origin: Option<Origin>,
}

/// A .NET exception: its type's full name, its message, the parameter it names, and the
/// fields a project's own exception type takes. One C# threw (a call into C#, a C# task) also
/// has its base types and its origin, the C# object the façade throws again (D-96).
#[derive(Clone)]
pub struct Exception {
    dotnet_type: Cow<'static, str>,
    message: String,
    param_name: Option<String>,
    fields: Vec<(String, String)>,
    // Boxed, as only some exceptions have them: a Result's error stays small.
    rare: Option<Box<Rare>>,
    managed: Option<Box<Managed>>,
}

/// What only some exceptions have: an actual value, a cancellation's token (D-97).
#[derive(Clone, Default)]
struct Rare {
    actual_value: Option<DotnetValue>,
    canceled_by: Option<CanceledBy>,
}

/// What a C# exception passing through native code has besides its type: its base types and
/// its origin.
#[derive(Clone)]
struct Managed {
    bases: Vec<String>,
    origin: Origin,
}

/// Equal: the same type, message, parameter, fields and actual value (a cancellation's token
/// isn't compared), and for a C# exception the same object.
impl PartialEq for Exception {
    fn eq(&self, other: &Exception) -> bool {
        self.dotnet_type == other.dotnet_type
            && self.message == other.message
            && self.param_name == other.param_name
            && self.fields == other.fields
            && self.actual_value() == other.actual_value()
            && match (&self.managed, &other.managed) {
                (None, None) => true,
                (Some(a), Some(b)) => a.bases == b.bases && Arc::ptr_eq(&a.origin, &b.origin),
                _ => false,
            }
    }
}

impl Eq for Exception {}

impl fmt::Debug for Exception {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Exception")
            .field("dotnet_type", &self.dotnet_type)
            .field("message", &self.message)
            .field("param_name", &self.param_name)
            .field("fields", &self.fields)
            .field("actual_value", &self.actual_value())
            .field("canceled_by_a_token", &self.canceled_by().is_some())
            .field("bases", &self.managed.as_ref().map(|m| &m.bases))
            .finish()
    }
}

/// The .NET base types of the exceptions a port throws itself, for [`Exception::is_a`].
fn known_bases(dotnet_type: &str) -> &'static [&'static str] {
    match dotnet_type {
        "System.ArgumentNullException" | "System.ArgumentOutOfRangeException" => {
            &["System.ArgumentException", "System.SystemException"]
        }
        "System.ObjectDisposedException" => {
            &["System.InvalidOperationException", "System.SystemException"]
        }
        "System.OverflowException" | "System.DivideByZeroException" => {
            &["System.ArithmeticException", "System.SystemException"]
        }
        "System.Threading.Tasks.TaskCanceledException" => {
            &["System.OperationCanceledException", "System.SystemException"]
        }
        "System.Exception" => &[],
        _ => &["System.SystemException"],
    }
}

const MANAGED: &str = "CodeCoreMorph.Managed";

impl Exception {
    /// An exception of the .NET type `dotnet_type` (`"System.InvalidOperationException"`).
    pub fn new(dotnet_type: impl Into<Cow<'static, str>>, message: impl Into<String>) -> Self {
        Exception {
            dotnet_type: dotnet_type.into(),
            message: message.into(),
            param_name: None,
            fields: Vec::new(),
            rare: None,
            managed: None,
        }
    }

    /// A project's own exception type: its .NET name and its constructor's arguments, by name,
    /// for the factory the port writes on the C# side (`CcmExceptions.cs`).
    pub fn custom<K, V>(
        dotnet_type: impl Into<Cow<'static, str>>,
        message: impl Into<String>,
        fields: impl IntoIterator<Item = (K, V)>,
    ) -> Self
    where
        K: Into<String>,
        V: Into<String>,
    {
        let mut e = Exception::new(dotnet_type, message);
        e.fields = fields.into_iter().map(|(k, v)| (k.into(), v.into())).collect();
        e
    }

    fn with_param(dotnet_type: &'static str, param_name: &str, message: String) -> Self {
        let mut e = Exception::new(dotnet_type, message);
        e.param_name = (!param_name.is_empty()).then(|| param_name.to_string());
        e
    }

    /// `ArgumentException(message, paramName)`.
    pub fn argument(message: impl Into<String>, param_name: &str) -> Self {
        Exception::with_param("System.ArgumentException", param_name, message.into())
    }

    /// `ArgumentNullException(paramName)`, with .NET's message.
    pub fn argument_null(param_name: &str) -> Self {
        Exception::with_param("System.ArgumentNullException", param_name, String::new())
    }

    /// `ArgumentNullException(paramName, message)`.
    pub fn argument_null_with(param_name: &str, message: impl Into<String>) -> Self {
        Exception::with_param("System.ArgumentNullException", param_name, message.into())
    }

    /// `ArgumentOutOfRangeException(paramName, message)`; an empty message is .NET's.
    pub fn argument_out_of_range(param_name: &str, message: impl Into<String>) -> Self {
        Exception::with_param("System.ArgumentOutOfRangeException", param_name, message.into())
    }

    /// `ArgumentOutOfRangeException(paramName, actualValue, message)`: the façade's exception
    /// reports the value as its ActualValue, and the message says it (D-97).
    pub fn argument_out_of_range_value(
        param_name: &str,
        actual_value: impl Into<DotnetValue>,
        message: impl Into<String>,
    ) -> Self {
        let mut e = Exception::argument_out_of_range(param_name, message);
        let value: DotnetValue = actual_value.into();
        if !value.is_null() {
            e.rare.get_or_insert_with(Box::default).actual_value = Some(value);
        }
        e
    }

    pub fn invalid_operation(message: impl Into<String>) -> Self {
        Exception::new("System.InvalidOperationException", message)
    }

    pub fn object_disposed(message: impl Into<String>) -> Self {
        Exception::new("System.ObjectDisposedException", message)
    }

    pub fn not_supported(message: impl Into<String>) -> Self {
        Exception::new("System.NotSupportedException", message)
    }

    pub fn not_implemented(message: impl Into<String>) -> Self {
        Exception::new("System.NotImplementedException", message)
    }

    pub fn key_not_found(message: impl Into<String>) -> Self {
        Exception::new("System.Collections.Generic.KeyNotFoundException", message)
    }

    pub fn index_out_of_range(message: impl Into<String>) -> Self {
        Exception::new("System.IndexOutOfRangeException", message)
    }

    /// `NullReferenceException`, with .NET's message: what a C# member access on null threw.
    pub fn null_reference() -> Self {
        Exception::new(
            "System.NullReferenceException",
            "Object reference not set to an instance of an object.",
        )
    }

    pub fn format(message: impl Into<String>) -> Self {
        Exception::new("System.FormatException", message)
    }

    pub fn arithmetic(message: impl Into<String>) -> Self {
        Exception::new("System.ArithmeticException", message)
    }

    /// `OverflowException`: checked C# arithmetic that overflowed, with .NET's message for it.
    pub fn overflow(message: impl Into<String>) -> Self {
        Exception::new("System.OverflowException", message)
    }

    /// `DivideByZeroException`, with .NET's message.
    pub fn divide_by_zero() -> Self {
        Exception::new("System.DivideByZeroException", "Attempted to divide by zero.")
    }

    /// `Microsoft.CSharp.RuntimeBinder.RuntimeBinderException`: what C#'s runtime binder threw
    /// where a dynamic site bound nothing for the value's type, with its message (D-88).
    pub fn runtime_binder(message: impl Into<String>) -> Self {
        Exception::new("Microsoft.CSharp.RuntimeBinder.RuntimeBinderException", message)
    }

    /// A call into C# (a callback, an interface a C# class implements) threw: the C# façade
    /// rethrows that exception itself. Code between passes it on unchanged.
    pub fn managed() -> Self {
        Exception::new(MANAGED, "a call into .NET threw")
    }

    /// The .NET type's full name.
    pub fn dotnet_type(&self) -> &str {
        &self.dotnet_type
    }

    /// The message, empty for .NET's own.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// The parameter an argument exception names.
    pub fn param_name(&self) -> Option<&str> {
        self.param_name.as_deref()
    }

    /// A project's exception's constructor arguments, by name.
    pub fn fields(&self) -> &[(String, String)] {
        &self.fields
    }

    /// An ArgumentOutOfRangeException's actual value.
    pub fn actual_value(&self) -> Option<&DotnetValue> {
        self.rare.as_ref().and_then(|r| r.actual_value.as_ref())
    }

    /// For the bridge, a cancellation's token (D-97): None where it has none that can be
    /// canceled; else the C# token it stands for, None for one native code made.
    pub fn token_origin(&self) -> Option<Option<&Origin>> {
        self.canceled_by().map(|c| c.origin.as_ref())
    }

    pub(crate) fn canceled_by(&self) -> Option<&CanceledBy> {
        self.rare.as_ref().and_then(|r| r.canceled_by.as_ref())
    }

    // The tasks module's, which a port without async code doesn't have.
    #[allow(dead_code)]
    pub(crate) fn set_canceled_by(&mut self, canceled_by: Option<CanceledBy>) {
        match canceled_by {
            Some(c) => self.rare.get_or_insert_with(Box::default).canceled_by = Some(c),
            None => {
                if let Some(rare) = self.rare.as_mut() {
                    rare.canceled_by = None;
                }
            }
        }
    }

    /// The value of one of [`Exception::fields`].
    pub fn field(&self, name: &str) -> Option<&str> {
        self.fields.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }

    /// Whether the exception is a C# one passing through ([`Exception::managed`], or one with
    /// its origin, [`Exception::from_managed`]).
    pub fn is_managed(&self) -> bool {
        self.dotnet_type == MANAGED || self.managed.is_some()
    }

    /// Whether the exception is of the .NET type `dotnet_type` (exactly, not a base type).
    pub fn is(&self, dotnet_type: &str) -> bool {
        self.dotnet_type == dotnet_type
    }

    /// Whether the exception is of `dotnet_type` or a type derived from it: what C#'s
    /// `catch (T)` catches (D-96).
    pub fn is_a(&self, dotnet_type: &str) -> bool {
        dotnet_type == "System.Exception"
            || self.is(dotnet_type)
            || self.managed.as_ref().is_some_and(|m| m.bases.iter().any(|b| b == dotnet_type))
            || known_bases(&self.dotnet_type).contains(&dotnet_type)
    }

    /// A C# exception that a call into C# (or a C# task) threw (D-96): its types, most derived
    /// first, its message, and its origin, the C# object the façade throws again.
    pub fn from_managed(types: Vec<String>, message: impl Into<String>, origin: Origin) -> Self {
        let mut types = types.into_iter();
        let first = types.next().unwrap_or_else(|| MANAGED.to_string());
        let mut e = Exception::new(first, message);
        e.managed = Some(Box::new(Managed { bases: types.collect(), origin }));
        e
    }

    /// The C# object a C# exception stands for ([`Exception::from_managed`]).
    pub fn origin(&self) -> Option<&Origin> {
        self.managed.as_ref().map(|m| &m.origin)
    }

    /// `OperationCanceledException`: what ThrowIfCancellationRequested throws.
    pub fn operation_canceled() -> Self {
        Exception::new("System.OperationCanceledException", "The operation was canceled.")
    }

    /// `TaskCanceledException`: what awaiting a canceled task throws.
    pub fn task_canceled() -> Self {
        Exception::new("System.Threading.Tasks.TaskCanceledException", "A task was canceled.")
    }

    /// Whether the exception cancels the task it ends, as an OperationCanceledException does.
    pub fn is_cancellation(&self) -> bool {
        self.is_a("System.OperationCanceledException")
    }
}

/// The message .NET gives an exception of `dotnet_type` constructed without one.
fn default_message(dotnet_type: &str) -> Option<&'static str> {
    Some(match dotnet_type {
        "System.ArgumentException" => "Value does not fall within the expected range.",
        "System.ArgumentNullException" => "Value cannot be null.",
        "System.ArgumentOutOfRangeException" => {
            "Specified argument was out of the range of valid values."
        }
        "System.InvalidOperationException" => {
            "Operation is not valid due to the current state of the object."
        }
        "System.ObjectDisposedException" => "Cannot access a disposed object.",
        "System.NotSupportedException" => "Specified method is not supported.",
        "System.NotImplementedException" => "The method or operation is not implemented.",
        "System.Collections.Generic.KeyNotFoundException" => {
            "The given key was not present in the dictionary."
        }
        "System.IndexOutOfRangeException" => "Index was outside the bounds of the array.",
        "System.NullReferenceException" => {
            "Object reference not set to an instance of an object."
        }
        "System.FormatException" => "One of the identified items was in an invalid format.",
        "System.ArithmeticException" => "Overflow or underflow in the arithmetic operation.",
        "System.OverflowException" => "Arithmetic operation resulted in an overflow.",
        "System.DivideByZeroException" => "Attempted to divide by zero.",
        _ => return None,
    })
}

/// .NET's `Exception.Message`: the message, or the type's own when there is none, and an
/// argument exception's parameter after it (`Value cannot be null. (Parameter 'plate')`).
impl fmt::Display for Exception {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if !self.message.is_empty() {
            write!(f, "{}", self.message)?;
        } else if let Some(message) = default_message(&self.dotnet_type) {
            write!(f, "{message}")?;
        } else {
            write!(f, "Exception of type '{}' was thrown.", self.dotnet_type)?;
        }
        if let Some(param) = &self.param_name {
            write!(f, " (Parameter '{param}')")?;
        }
        if let Some(value) = self.actual_value() {
            let newline = if cfg!(windows) { "\r\n" } else { "\n" };
            write!(f, "{newline}Actual value was {}.", value.shown())?;
        }
        Ok(())
    }
}

impl Error for Exception {}
