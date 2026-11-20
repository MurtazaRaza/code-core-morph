package org.example.warehouse;

/**
 * A single part, of a fixed weight per piece.
 */
public class Part extends Product {
    private final int gramsEach;

    public Part(Sku sku, String name, int gramsEach) {
        super(sku, name);
        if (gramsEach <= 0) {
            throw new IllegalArgumentException("a part weighs something: " + gramsEach);
        }
        this.gramsEach = gramsEach;
    }

    public int getGramsEach() {
        return gramsEach;
    }

    @Override
    public int weightGrams(int pieces) {
        return pieces * gramsEach;
    }
}
