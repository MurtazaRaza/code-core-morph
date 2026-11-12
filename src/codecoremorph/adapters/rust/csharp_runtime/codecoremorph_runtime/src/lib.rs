//! What a Rust port of C# code needs of .NET: its decimal ([`Decimal`]) and its exceptions
//! ([`Exception`]). Written by CodeCoreMorph; do not edit.
#![forbid(unsafe_code)]

pub mod decimal;
pub mod exceptions;

pub use decimal::Decimal;
pub use exceptions::{Exception, Result};
