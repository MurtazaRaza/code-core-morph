package org.example.warehouse;

/**
 * Thrown when a shelf can't take a product's weight.
 */
public class ShelfFullException extends Exception {
    private static final long serialVersionUID = 1L;

    private final int overGrams;

    public ShelfFullException(String shelf, int overGrams) {
        super("shelf " + shelf + " is full: " + overGrams + " g over");
        this.overGrams = overGrams;
    }

    public int getOverGrams() {
        return overGrams;
    }
}
