//! Java's exceptions, by type name, message and fields: what the ported code returns as its
//! error, and the façade throws again.
use std::fmt;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Exception {
    java_type: String,
    message: Option<String>,
    fields: Vec<(String, String)>,
}

impl Exception {
    pub fn new(java_type: &str, message: impl Into<String>) -> Self {
        Exception { java_type: java_type.to_string(), message: Some(message.into()), fields: Vec::new() }
    }

    /// One with no message (Java's null).
    pub fn bare(java_type: &str) -> Self {
        Exception { java_type: java_type.to_string(), message: None, fields: Vec::new() }
    }

    /// A project's own exception: its constructor's arguments by name, for the façade's factory.
    pub fn with_fields(java_type: &str, message: impl Into<String>, fields: Vec<(String, String)>) -> Self {
        Exception { java_type: java_type.to_string(), message: Some(message.into()), fields }
    }

    pub fn illegal_argument(message: impl Into<String>) -> Self {
        Self::new("java.lang.IllegalArgumentException", message)
    }
    pub fn illegal_state(message: impl Into<String>) -> Self {
        Self::new("java.lang.IllegalStateException", message)
    }
    pub fn number_format(message: impl Into<String>) -> Self {
        Self::new("java.lang.NumberFormatException", message)
    }
    pub fn no_such_element(message: impl Into<String>) -> Self {
        Self::new("java.util.NoSuchElementException", message)
    }
    pub fn arithmetic(message: impl Into<String>) -> Self {
        Self::new("java.lang.ArithmeticException", message)
    }
    pub fn index_out_of_bounds(message: impl Into<String>) -> Self {
        Self::new("java.lang.IndexOutOfBoundsException", message)
    }
    /// NullPointerException, with the message the Java's check gave (Objects.requireNonNull).
    pub fn null_pointer(message: Option<&str>) -> Self {
        match message {
            Some(m) => Self::new("java.lang.NullPointerException", m),
            None => Self::bare("java.lang.NullPointerException"),
        }
    }
    /// A call into Java threw: the façade rethrows that exception itself.
    pub fn managed() -> Self {
        Self::bare("codecoremorph.Managed")
    }

    pub fn java_type(&self) -> &str {
        &self.java_type
    }
    pub fn message(&self) -> Option<&str> {
        self.message.as_deref()
    }
    pub fn fields(&self) -> &[(String, String)] {
        &self.fields
    }
    pub fn is_managed(&self) -> bool {
        self.java_type == "codecoremorph.Managed"
    }
}

impl fmt::Display for Exception {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.message {
            Some(m) => write!(f, "{}: {}", self.java_type, m),
            None => f.write_str(&self.java_type),
        }
    }
}

impl std::error::Error for Exception {}
