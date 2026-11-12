//! Errors that mirror .NET's exceptions, for Rust ports of C# code. Written by CodeCoreMorph;
//! do not edit.
//!
//! An [`Exception`] names the .NET type it stands for, so while a port keeps C# façades the
//! bridge hands the façade what to throw; after the port it is the crate's error type. A
//! project's own exception type is [`Exception::custom`], with its .NET type and the arguments
//! its constructor takes, by name. An empty message is the one .NET gives the type.

use std::borrow::Cow;
use std::error::Error;
use std::fmt;

/// What a ported member returns where the C# could throw.
pub type Result<T, E = Exception> = std::result::Result<T, E>;

/// A .NET exception: its type's full name, its message, the parameter it names, and the
/// fields a project's own exception type takes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exception {
    dotnet_type: Cow<'static, str>,
    message: String,
    param_name: Option<String>,
    fields: Vec<(String, String)>,
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

    /// The value of one of [`Exception::fields`].
    pub fn field(&self, name: &str) -> Option<&str> {
        self.fields.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
    }

    /// Whether the exception is a C# one passing through ([`Exception::managed`]).
    pub fn is_managed(&self) -> bool {
        self.dotnet_type == MANAGED
    }

    /// Whether the exception is of the .NET type `dotnet_type` (exactly, not a base type).
    pub fn is(&self, dotnet_type: &str) -> bool {
        self.dotnet_type == dotnet_type
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
        Ok(())
    }
}

impl Error for Exception {}
