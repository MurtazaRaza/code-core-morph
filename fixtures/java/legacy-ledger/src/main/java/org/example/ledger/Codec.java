package org.example.ledger;

import java.io.IOException;
import java.nio.charset.Charset;
import java.util.zip.CRC32;
import javax.xml.bind.DatatypeConverter;
import sun.misc.BASE64Decoder;
import sun.misc.BASE64Encoder;

/** Encodings for exporting a ledger: Base64 (MIME style, 76 characters a line) and hex. */
public final class Codec {
    private static final Charset UTF8 = Charset.forName("UTF-8");

    private Codec() {
    }

    public static String toBase64(String text) {
        return new BASE64Encoder().encode(text.getBytes(UTF8));
    }

    public static String fromBase64(String encoded) throws IOException {
        return new String(new BASE64Decoder().decodeBuffer(encoded), UTF8);
    }

    /** Upper-case hex of the text's UTF-8 bytes. */
    public static String toHex(String text) {
        return DatatypeConverter.printHexBinary(text.getBytes(UTF8));
    }

    /** The CRC-32 of the text, as eight upper-case hex digits. */
    public static String checksum(String text) {
        CRC32 crc = new CRC32();
        crc.update(text.getBytes(UTF8));
        String hex = Long.toHexString(crc.getValue()).toUpperCase();
        while (hex.length() < 8) {
            hex = "0" + hex;
        }
        return hex;
    }
}
