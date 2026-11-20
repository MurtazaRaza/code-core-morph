//! The messages, and frames around them: a header, the payload, the payload's CRC.

use crate::codec::{Decode, DecodeError, Encode};
use crate::crc::crc16;
use wire_macros::{throws, Decode, Encode};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Encode, Decode)]
pub struct Header {
    pub kind: u8,
    pub len: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Encode, Decode)]
pub struct Reading {
    pub sensor: u16,
    pub value: i32,
    pub valid: bool,
    /// When the reading was last looked at; local, never sent.
    #[wire(skip)]
    pub seen: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Encode, Decode)]
pub enum Command {
    Ping,
    Set { key: u8, value: u32 },
    Reset(u16),
    Report(Header, Reading),
}

/// A frame of `kind` around `payload`.
pub fn write_frame(kind: u8, payload: &[u8]) -> Vec<u8> {
    let header = Header { kind, len: payload.len() as u16 };
    let mut out = Vec::with_capacity(header.encoded_len() + payload.len() + 2);
    header.encode(&mut out);
    out.extend_from_slice(payload);
    crc16(payload).encode(&mut out);
    out
}

/// The header and payload of the frame at the start of `bytes`, and the bytes after it.
#[throws(DecodeError)]
pub fn read_frame(bytes: &[u8]) -> (Header, Vec<u8>, usize) {
    let mut input = bytes;
    let header = Header::decode(&mut input)?;
    let len = header.len as usize;
    if input.len() < len + 2 {
        return Err(DecodeError::Short);
    }
    let payload = &input[..len];
    let found = u16::decode(&mut &input[len..])?;
    let expected = crc16(payload);
    if found != expected {
        return Err(DecodeError::BadCrc { expected, found });
    }
    (header, payload.to_vec(), bytes.len() - input.len() + len + 2)
}

/// A command in a frame of kind 1.
pub fn command_frame(command: &Command) -> Vec<u8> {
    let mut payload = Vec::new();
    command.encode(&mut payload);
    write_frame(1, &payload)
}

/// The command in a frame of kind 1.
#[throws(DecodeError)]
pub fn read_command(bytes: &[u8]) -> Command {
    let (header, payload, _) = read_frame(bytes)?;
    if header.kind != 1 {
        return Err(DecodeError::BadTag(header.kind));
    }
    let mut input = &payload[..];
    Command::decode(&mut input)?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_reading_skips_seen() {
        let r = Reading { sensor: 7, value: -40, valid: true, seen: 99 };
        let mut out = Vec::new();
        r.encode(&mut out);
        assert_eq!(out, [7, 0, 0xd8, 0xff, 0xff, 0xff, 1]);
        assert_eq!(r.encoded_len(), 7);
        let back = Reading::decode(&mut &out[..]).unwrap();
        assert_eq!(back, Reading { seen: 0, ..r });
    }

    #[test]
    fn a_command_round_trips() {
        let c = Command::Set { key: 3, value: 70000 };
        assert_eq!(read_command(&command_frame(&c)), Ok(c));
        assert_eq!(Command::Ping.encoded_len(), 1);
    }

    #[test]
    fn a_bad_tag_and_a_bad_crc() {
        assert_eq!(Command::decode(&mut &[9u8][..]), Err(DecodeError::BadTag(9)));
        let mut frame = write_frame(2, b"abc");
        let last = frame.len() - 1;
        frame[last] ^= 1;
        assert!(matches!(read_frame(&frame), Err(DecodeError::BadCrc { .. })));
    }
}
