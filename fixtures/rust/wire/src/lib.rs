//! wire: a small framed binary message format.

// The derives name `::wire::codec`, inside the crate as well as outside it.
extern crate self as wire;

pub mod codec;
pub mod crc;
pub mod msg;
