use wire::codec::{Decode, DecodeError, Encode};
use wire::msg::{command_frame, read_command, read_frame, write_frame, Command, Header, Reading};
use wire_macros::{Decode, Encode};

/// A message only the tests send.
#[derive(Debug, PartialEq, Encode, Decode)]
struct Probe {
    id: u32,
    #[wire(skip)]
    note: u8,
    echo: bool,
}

#[test]
fn frames_follow_each_other() {
    let mut bytes = write_frame(2, b"hello");
    bytes.extend(command_frame(&Command::Reset(513)));
    let (header, payload, used) = read_frame(&bytes).unwrap();
    assert_eq!(header, Header { kind: 2, len: 5 });
    assert_eq!(payload, b"hello");
    assert_eq!(read_command(&bytes[used..]), Ok(Command::Reset(513)));
}

#[test]
fn a_report_nests_its_parts() {
    let c = Command::Report(
        Header { kind: 9, len: 0 },
        Reading { sensor: 1, value: 2, valid: false, seen: 3 },
    );
    let frame = command_frame(&c);
    assert_eq!(frame.len(), 3 + 1 + 3 + 7 + 2);
    let back = read_command(&frame).unwrap();
    assert_eq!(
        back,
        Command::Report(
            Header { kind: 9, len: 0 },
            Reading { sensor: 1, value: 2, valid: false, seen: 0 }
        )
    );
}

#[test]
fn a_test_message_round_trips() {
    let p = Probe { id: 0xdeadbeef, note: 5, echo: true };
    let mut out = Vec::new();
    p.encode(&mut out);
    assert_eq!(out, [0xef, 0xbe, 0xad, 0xde, 1]);
    assert_eq!(Probe::decode(&mut &out[..]), Ok(Probe { note: 0, ..p }));
}

#[test]
fn a_short_frame() {
    assert_eq!(read_frame(&[1, 4, 0, 1]), Err(DecodeError::Short));
    assert_eq!(read_command(&write_frame(3, b"")), Err(DecodeError::BadTag(3)));
}
