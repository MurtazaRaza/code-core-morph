//! The encoding of each value: integers little-endian, a `bool` one byte.

use std::fmt;

/// Writes a value as bytes.
pub trait Encode {
    fn encode(&self, out: &mut Vec<u8>);
    fn encoded_len(&self) -> usize;
}

/// Reads a value back, taking its bytes off the front of `input`.
pub trait Decode: Sized {
    fn decode(input: &mut &[u8]) -> Result<Self, DecodeError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    /// The input ended inside a value.
    Short,
    /// An enum's tag no variant has.
    BadTag(u8),
    /// A `bool` that is neither 0 nor 1.
    BadBool(u8),
    /// A frame whose checksum isn't its payload's.
    BadCrc { expected: u16, found: u16 },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DecodeError::Short => write!(f, "input ended inside a value"),
            DecodeError::BadTag(t) => write!(f, "no variant has tag {}", t),
            DecodeError::BadBool(b) => write!(f, "{} is not a bool", b),
            DecodeError::BadCrc { expected, found } => {
                write!(f, "checksum {:04x}, expected {:04x}", found, expected)
            }
        }
    }
}

fn take<'a>(input: &mut &'a [u8], n: usize) -> Result<&'a [u8], DecodeError> {
    if input.len() < n {
        return Err(DecodeError::Short);
    }
    let (head, rest) = input.split_at(n);
    *input = rest;
    Ok(head)
}

macro_rules! integer {
    ($($t:ty),*) => {$(
        impl Encode for $t {
            fn encode(&self, out: &mut Vec<u8>) {
                out.extend_from_slice(&self.to_le_bytes());
            }
            fn encoded_len(&self) -> usize {
                std::mem::size_of::<$t>()
            }
        }
        impl Decode for $t {
            fn decode(input: &mut &[u8]) -> Result<Self, DecodeError> {
                let mut bytes = [0u8; std::mem::size_of::<$t>()];
                bytes.copy_from_slice(take(input, std::mem::size_of::<$t>())?);
                Ok(<$t>::from_le_bytes(bytes))
            }
        }
    )*};
}

integer!(u8, u16, u32, i32);

impl Encode for bool {
    fn encode(&self, out: &mut Vec<u8>) {
        out.push(*self as u8);
    }
    fn encoded_len(&self) -> usize {
        1
    }
}

impl Decode for bool {
    fn decode(input: &mut &[u8]) -> Result<Self, DecodeError> {
        match take(input, 1)?[0] {
            0 => Ok(false),
            1 => Ok(true),
            other => Err(DecodeError::BadBool(other)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn integers_are_little_endian() {
        let mut out = Vec::new();
        0x1234u16.encode(&mut out);
        (-2i32).encode(&mut out);
        assert_eq!(out, [0x34, 0x12, 0xfe, 0xff, 0xff, 0xff]);
        let mut input = &out[..];
        assert_eq!(u16::decode(&mut input), Ok(0x1234));
        assert_eq!(i32::decode(&mut input), Ok(-2));
        assert!(input.is_empty());
    }

    #[test]
    fn short_input_and_bad_bool() {
        assert_eq!(u32::decode(&mut &[1u8, 2][..]), Err(DecodeError::Short));
        assert_eq!(bool::decode(&mut &[7u8][..]), Err(DecodeError::BadBool(7)));
        assert_eq!(DecodeError::BadTag(9).to_string(), "no variant has tag 9");
    }
}
