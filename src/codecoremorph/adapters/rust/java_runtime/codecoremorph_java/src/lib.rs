//! Java's exceptions and semantics, for Rust ports of Java code (CodeCoreMorph, D-92). Written
//! by CodeCoreMorph; do not edit. The ported code returns `Result<T, Exception>`, an
//! [`Exception`] naming the Java type it stands for, and keeps Java's arithmetic and string
//! rules with [`lang`]. It stays after the retirement: the ported code uses it.
#![forbid(unsafe_code)]

mod exception;
pub mod lang;

pub use exception::Exception;
