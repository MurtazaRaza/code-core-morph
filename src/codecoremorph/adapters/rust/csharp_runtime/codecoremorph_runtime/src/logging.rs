//! Microsoft.Extensions.Logging.Abstractions for a Rust port of C# code (CodeCoreMorph, D-95).
//! Written by CodeCoreMorph; do not edit.
//!
//! As in .NET, a logger is an object the code is given: [`Logger`], which says whether a level
//! is enabled and logs a message, and [`LoggerFactory`], which makes one per category.
//! [`NullLogger`] logs nothing. [`log_debug`] and its kin are LoggerExtensions' `LogDebug` and
//! kin: they fill the message template's holes (`"Stored {Key}"`) as .NET's formatter does, the
//! n-th hole taking the n-th argument whatever its name, and call [`Logger::log`], whether or
//! not the level is enabled (the logger decides, as in .NET); [`log`] takes an exception too,
//! for `LogError(exception, ...)`. A hole's alignment (`{Count,5}`) pads; a format
//! (`{Count:X4}`) is refused. A logger's calls return [`Result`]: a C# logger the port still
//! calls through its façade may throw. A logger gets the entry's state too
//! ([`Logger::log_state`], D-97): the template and each hole's name and value, as .NET's
//! structured loggers do; an argument's [`LogArg::log_value`] is its [`DotnetValue`], so a
//! `Duration` is a TimeSpan, and a project's own type (its TimeSpan, an enum) implements
//! [`LogArg`] with both its text and its value. The retirement keeps this module.
use std::sync::{Arc, OnceLock};

use crate::exceptions::{floating_text, time_span_text};
use crate::{DotnetValue, Exception, Result};

/// .NET's `LogLevel`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[repr(i32)]
pub enum LogLevel {
    Trace = 0,
    Debug = 1,
    Information = 2,
    Warning = 3,
    Error = 4,
    Critical = 5,
    None = 6,
}

impl LogLevel {
    /// The level of its .NET value; None for one outside the enum.
    pub fn from_i32(value: i32) -> Option<LogLevel> {
        Some(match value {
            0 => LogLevel::Trace,
            1 => LogLevel::Debug,
            2 => LogLevel::Information,
            3 => LogLevel::Warning,
            4 => LogLevel::Error,
            5 => LogLevel::Critical,
            6 => LogLevel::None,
            _ => return None,
        })
    }
}

/// .NET's `EventId`: a number and an optional name.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct EventId {
    pub id: i32,
    pub name: Option<String>,
}

impl EventId {
    pub fn new(id: i32, name: impl Into<String>) -> EventId {
        EventId { id, name: Some(name.into()) }
    }
}

/// A log entry's state, as LoggerExtensions makes it (FormattedLogValues, D-97): the message
/// (the template with its holes filled), the template, and each hole's name with its
/// argument's value, in order. A C# logger gets them as .NET's state: the names and values,
/// then `"{OriginalFormat}"` and the template.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct LogState {
    pub message: String,
    pub template: String,
    pub values: Vec<(String, DotnetValue)>,
}

/// .NET's `ILogger`. The message is the template with its holes filled; the exception is the
/// one the C# passed (`LogError(exception, ...)`), or None.
pub trait Logger: Send + Sync {
    fn is_enabled(&self, level: LogLevel) -> Result<bool>;
    fn log(
        &self,
        level: LogLevel,
        event: &EventId,
        message: &str,
        exception: Option<&Exception>,
    ) -> Result<()>;

    /// An entry with its state, which [`log`] and its kin make: by default, [`Logger::log`]
    /// with its message. The C# logger behind a façade gets the state.
    fn log_state(
        &self,
        level: LogLevel,
        event: &EventId,
        state: &LogState,
        exception: Option<&Exception>,
    ) -> Result<()> {
        self.log(level, event, &state.message, exception)
    }
}

/// .NET's `ILoggerFactory`.
pub trait LoggerFactory: Send + Sync {
    fn create_logger(&self, category: &str) -> Result<Arc<dyn Logger>>;
}

/// `NullLogger.Instance`: enabled for nothing, logs nothing.
#[derive(Clone, Copy, Debug, Default)]
pub struct NullLogger;

impl NullLogger {
    pub fn instance() -> Arc<dyn Logger> {
        static INSTANCE: OnceLock<Arc<dyn Logger>> = OnceLock::new();
        INSTANCE.get_or_init(|| Arc::new(NullLogger)).clone()
    }
}

impl Logger for NullLogger {
    fn is_enabled(&self, _level: LogLevel) -> Result<bool> {
        Ok(false)
    }

    fn log(
        &self,
        _level: LogLevel,
        _event: &EventId,
        _message: &str,
        _exception: Option<&Exception>,
    ) -> Result<()> {
        Ok(())
    }
}

/// A log entry's argument: its text as .NET's invariant culture writes it in a message, and the
/// value the entry's state keeps (by default its text, a string).
pub trait LogArg {
    fn log_text(&self) -> String;

    fn log_value(&self) -> DotnetValue {
        DotnetValue::new("System.String", self.log_text())
    }
}

impl LogArg for str {
    fn log_text(&self) -> String {
        self.to_string()
    }
}

impl LogArg for String {
    fn log_text(&self) -> String {
        self.clone()
    }
}

impl<T: LogArg + ?Sized> LogArg for &T {
    fn log_text(&self) -> String {
        (**self).log_text()
    }

    fn log_value(&self) -> DotnetValue {
        (**self).log_value()
    }
}

impl LogArg for bool {
    fn log_text(&self) -> String {
        if *self { "True" } else { "False" }.to_string()
    }

    fn log_value(&self) -> DotnetValue {
        DotnetValue::from(*self)
    }
}

impl LogArg for char {
    fn log_text(&self) -> String {
        self.to_string()
    }

    fn log_value(&self) -> DotnetValue {
        DotnetValue::from(*self)
    }
}

macro_rules! integer_args {
    ($($t:ty),*) => {
        $(impl LogArg for $t {
            fn log_text(&self) -> String {
                self.to_string()
            }

            fn log_value(&self) -> DotnetValue {
                DotnetValue::from(*self)
            }
        })*
    };
}

integer_args!(i8, i16, i32, i64, isize, u8, u16, u32, u64, usize);

// .NET has no 128-bit integers in a log entry's state before .NET 7's Int128: their text.
impl LogArg for i128 {
    fn log_text(&self) -> String {
        self.to_string()
    }
}

impl LogArg for u128 {
    fn log_text(&self) -> String {
        self.to_string()
    }
}

/// A duration is a TimeSpan: `[-][d.]hh:mm:ss[.fffffff]`.
impl LogArg for std::time::Duration {
    fn log_text(&self) -> String {
        time_span_text(i64::try_from(self.as_nanos() / 100).unwrap_or(i64::MAX))
    }

    fn log_value(&self) -> DotnetValue {
        DotnetValue::from(*self)
    }
}

impl LogArg for DotnetValue {
    fn log_text(&self) -> String {
        if self.is_null() { "(null)".to_string() } else { self.shown().to_string() }
    }

    fn log_value(&self) -> DotnetValue {
        self.clone()
    }
}

/// The shortest text that reads back as the same double, as .NET Core writes it: `1.5`,
/// `1E+20`, `1E-05`, `NaN`, `Infinity`.
impl LogArg for f64 {
    fn log_text(&self) -> String {
        floating_text(*self, &format!("{self:e}"), 17)
    }

    fn log_value(&self) -> DotnetValue {
        DotnetValue::from(*self)
    }
}

impl LogArg for f32 {
    fn log_text(&self) -> String {
        floating_text(f64::from(*self), &format!("{self:e}"), 9)
    }

    fn log_value(&self) -> DotnetValue {
        DotnetValue::from(*self)
    }
}

impl<T: LogArg> LogArg for Option<T> {
    fn log_text(&self) -> String {
        self.as_ref().map_or_else(|| "(null)".to_string(), LogArg::log_text)
    }

    fn log_value(&self) -> DotnetValue {
        self.as_ref().map_or_else(DotnetValue::null, LogArg::log_value)
    }
}

/// A sequence is its items, comma-separated, as .NET writes an `IEnumerable` argument.
impl<T: LogArg> LogArg for [T] {
    fn log_text(&self) -> String {
        self.iter().map(LogArg::log_text).collect::<Vec<_>>().join(", ")
    }
}

impl<T: LogArg> LogArg for Vec<T> {
    fn log_text(&self) -> String {
        self.as_slice().log_text()
    }
}

fn aligned(value: String, alignment: &str) -> Result<String> {
    if alignment.is_empty() {
        return Ok(value);
    }
    let (left, digits) = match alignment.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, alignment),
    };
    let width: usize = digits.trim().parse().map_err(|_| {
        Exception::format("A message template hole's alignment isn't a number.")
    })?;
    let length = value.chars().count();
    if length >= width {
        return Ok(value);
    }
    let pad = " ".repeat(width - length);
    Ok(if left { value + &pad } else { pad + &value })
}

/// The state .NET's LoggerExtensions makes of a template and its arguments: the message its
/// formatter writes, and each hole's name and value (a hole without an argument fails, an
/// argument without a hole is left out, as in .NET).
pub fn state(template: &str, args: &[&dyn LogArg]) -> Result<LogState> {
    let mut out = LogState { message: String::new(), template: template.to_string(), values: Vec::new() };
    // Without arguments .NET logs the template as it is, holes and doubled braces too.
    if args.is_empty() {
        out.message = template.to_string();
        return Ok(out);
    }
    let mut next = 0;
    let mut rest = template;
    while let Some(at) = rest.find(['{', '}']) {
        out.message.push_str(&rest[..at]);
        let brace = &rest[at..at + 1];
        let after = &rest[at + 1..];
        if after.starts_with(brace) {
            out.message.push_str(brace);
            rest = &after[1..];
            continue;
        }
        if brace == "}" {
            out.message.push('}');
            rest = after;
            continue;
        }
        let Some(close) = after.find('}') else {
            out.message.push_str(&rest[at..]);
            return Ok(out);
        };
        let hole = &after[..close];
        if hole.contains(':') {
            return Err(Exception::not_supported(
                "A message template hole's format isn't supported in the port.",
            ));
        }
        let (name, alignment) = hole.split_once(',').unwrap_or((hole, ""));
        let arg = args.get(next).ok_or_else(|| {
            Exception::format("A message template has more holes than arguments.")
        })?;
        out.message.push_str(&aligned(arg.log_text(), alignment)?);
        out.values.push((name.to_string(), arg.log_value()));
        next += 1;
        rest = &after[close + 1..];
    }
    out.message.push_str(rest);
    Ok(out)
}

/// The message .NET's formatter makes of a template and its arguments.
pub fn format(template: &str, args: &[&dyn LogArg]) -> Result<String> {
    Ok(state(template, args)?.message)
}

/// `logger.Log(level, event, exception, template, args)`: every form of LoggerExtensions' (an
/// exception for `LogError(exception, ...)`, the default `EventId` where the C# passed none).
pub fn log(
    logger: &dyn Logger,
    level: LogLevel,
    event: &EventId,
    exception: Option<&Exception>,
    template: &str,
    args: &[&dyn LogArg],
) -> Result<()> {
    logger.log_state(level, event, &state(template, args)?, exception)
}

macro_rules! levels {
    ($($name:ident, $with:ident, $level:ident;)*) => {
        $(
            #[doc = concat!("`Log", stringify!($level), "(template, args)`.")]
            pub fn $name(logger: &dyn Logger, template: &str, args: &[&dyn LogArg]) -> Result<()> {
                log(logger, LogLevel::$level, &EventId::default(), None, template, args)
            }

            #[doc = concat!("`Log", stringify!($level), "(event, template, args)`.")]
            pub fn $with(
                logger: &dyn Logger,
                event: &EventId,
                template: &str,
                args: &[&dyn LogArg],
            ) -> Result<()> {
                log(logger, LogLevel::$level, event, None, template, args)
            }
        )*
    };
}

levels! {
    log_trace, log_trace_event, Trace;
    log_debug, log_debug_event, Debug;
    log_information, log_information_event, Information;
    log_warning, log_warning_event, Warning;
    log_error, log_error_event, Error;
    log_critical, log_critical_event, Critical;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn holes_take_the_arguments_in_order() {
        let n = 64;
        assert_eq!(
            format("Stored {Key}: {Raw} bytes in {Packed}", &[&"a", &n, &15usize]).unwrap(),
            "Stored a: 64 bytes in 15"
        );
        assert_eq!(format("{{literal}} {A,4}|{B,-3}|", &[&1, &2]).unwrap(), "{literal}    1|2  |");
        assert_eq!(format("Removed {Key}", &[]).unwrap(), "Removed {Key}");
        assert_eq!(format("{A} {B}", &[&true, &Option::<i32>::None]).unwrap(), "True (null)");
        assert_eq!(format("{V}", &[&1e20f64]).unwrap(), "1E+20");
        assert_eq!(format("{V}", &[&1.5e-5f64]).unwrap(), "1.5E-05");
        assert_eq!(format("{V}", &[&0.25f64]).unwrap(), "0.25");
        assert_eq!(format("{V}", &[&0.0001f64]).unwrap(), "0.0001");
        assert_eq!(format("{V}", &[&1e15f64]).unwrap(), "1000000000000000");
        assert_eq!(format("{V}", &[&1e17f64]).unwrap(), "1E+17");
        assert!(format("{V:X4}", &[&1]).is_err());
        assert_eq!(format("{V}", &[&1e9f32]).unwrap(), "1E+09");
        assert_eq!(format("{V}", &[&123456790f32]).unwrap(), "123456790");
        assert_eq!(format("{V}", &[&0.1f32]).unwrap(), "0.1");
    }

    #[test]
    fn the_state_names_each_hole_with_its_value() {
        let delay = std::time::Duration::from_millis(1500);
        let made = state("Attempt {Attempt} of {Max,3} in {Delay}", &[&2, &3i64, &delay, &"x"]).unwrap();
        assert_eq!(made.message, "Attempt 2 of   3 in 00:00:01.5000000");
        assert_eq!(made.template, "Attempt {Attempt} of {Max,3} in {Delay}");
        assert_eq!(
            made.values,
            vec![
                ("Attempt".to_string(), DotnetValue::new("System.Int32", "2")),
                ("Max".to_string(), DotnetValue::new("System.Int64", "3")),
                ("Delay".to_string(), DotnetValue::time_span(15_000_000)),
            ]
        );
        assert!(state("{A}", &[&Option::<i32>::None]).unwrap().values[0].1.is_null());
        assert!(state("No holes", &[]).unwrap().values.is_empty());
    }
}
