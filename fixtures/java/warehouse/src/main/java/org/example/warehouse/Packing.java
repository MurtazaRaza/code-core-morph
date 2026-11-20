package org.example.warehouse;

/**
 * How goods are packed, with how many pieces a pack holds.
 */
public enum Packing {
    PIECE("pc", 1),
    DOZEN("dz", 12),
    CRATE("cr", 48);

    private final String symbol;
    private final int pieces;

    Packing(String symbol, int pieces) {
        this.symbol = symbol;
        this.pieces = pieces;
    }

    public String symbol() {
        return symbol;
    }

    /** The pieces in this many packs. */
    public int toPieces(int packs) {
        return packs * pieces;
    }

    /** The packing with this symbol; IllegalArgumentException for none. */
    public static Packing bySymbol(String symbol) {
        for (Packing p : values()) {
            if (p.symbol.equals(symbol)) {
                return p;
            }
        }
        throw new IllegalArgumentException("no packing " + symbol);
    }
}
