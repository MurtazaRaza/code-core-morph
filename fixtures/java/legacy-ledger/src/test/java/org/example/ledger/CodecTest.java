package org.example.ledger;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class CodecTest {
    private static final String LONG =
            "2024-03-01,Salary,2500.00,income;2024-02-28,Rent,-900.00,rent;2024-03-02,Groceries";

    @Test
    public void encodesBase64() {
        assertEquals("bGVkZ2Vy", Codec.toBase64("ledger"));
        assertEquals("w6l0w6k=", Codec.toBase64("été"));
    }

    @Test
    public void wrapsLongBase64Lines() {
        String encoded = Codec.toBase64(LONG);
        String[] lines = encoded.split("\r?\n", -1);
        assertEquals(2, lines.length);
        assertEquals(76, lines[0].length());
        assertEquals("MjAyNC0wMy0wMSxTYWxhcnksMjUwMC4wMCxpbmNvbWU7MjAyNC0wMi0yOCxSZW50LC05MDAuMDAs", lines[0]);
    }

    @Test
    public void decodesWhatItEncodes() throws Exception {
        assertEquals(LONG, Codec.fromBase64(Codec.toBase64(LONG)));
        assertEquals("été", Codec.fromBase64("w6l0w6k="));
    }

    @Test
    public void writesUpperCaseHex() {
        assertEquals("4C6564676572", Codec.toHex("Ledger"));
        assertEquals("", Codec.toHex(""));
    }

    @Test
    public void checksumsText() {
        assertEquals("CBF43926", Codec.checksum("123456789"));
        assertEquals("00000000", Codec.checksum(""));
    }
}
