package org.example.warehouse;

/**
 * Something the warehouse stocks.
 */
public abstract class Product {
    private final Sku sku;
    private final String name;

    protected Product(Sku sku, String name) {
        if (sku == null) {
            throw new NullPointerException("sku");
        }
        this.sku = sku;
        this.name = name;
    }

    public Sku getSku() {
        return sku;
    }

    public String getName() {
        return name;
    }

    /** What this many pieces weigh, in grams. */
    public abstract int weightGrams(int pieces);

    /** Whether it ships as freight: over 30 kg for the quantity. */
    public boolean isFreight(int pieces) {
        return weightGrams(pieces) > 30000;
    }

    /** A shipping label, "AB-1234-7 Bolt x10 (1.5 kg)". */
    public String label(int pieces) {
        int grams = weightGrams(pieces);
        return sku + " " + name + " x" + pieces + " (" + (grams / 1000) + "." + (grams % 1000 / 100)
            + " kg)";
    }
}
