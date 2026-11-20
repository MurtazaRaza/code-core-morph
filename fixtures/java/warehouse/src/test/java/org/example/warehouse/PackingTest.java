package org.example.warehouse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;

import java.util.Arrays;
import java.util.Collection;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;
import org.junit.runners.Parameterized.Parameters;

@RunWith(Parameterized.class)
public class PackingTest {
    @Parameters(name = "{0}")
    public static Collection<Object[]> cases() {
        return Arrays.asList(new Object[][] {
            {"pc", 3, 3},
            {"dz", 2, 24},
            {"cr", 1, 48},
        });
    }

    private final String symbol;
    private final int packs;
    private final int pieces;

    public PackingTest(String symbol, int packs, int pieces) {
        this.symbol = symbol;
        this.packs = packs;
        this.pieces = pieces;
    }

    @Test
    public void countsPieces() {
        Packing packing = Packing.bySymbol(symbol);
        assertEquals(pieces, packing.toPieces(packs));
        assertSame(packing, Packing.valueOf(packing.name()));
        assertEquals(symbol, packing.symbol());
    }
}
