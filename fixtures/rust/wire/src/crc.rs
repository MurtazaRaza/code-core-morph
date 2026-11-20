//! CRC-16/ARC over a frame's payload.

use wire_macros::crc_table;

const TABLE: [u16; 256] = crc_table!(0xA001);

/// The CRC-16/ARC of `data` (reflected, polynomial 0x8005, initial value 0).
pub fn crc16(data: &[u8]) -> u16 {
    data.iter()
        .fold(0u16, |crc, &b| (crc >> 8) ^ TABLE[((crc ^ b as u16) & 0xff) as usize])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn check_value() {
        assert_eq!(crc16(b"123456789"), 0xBB3D);
        assert_eq!(crc16(b""), 0);
    }
}
