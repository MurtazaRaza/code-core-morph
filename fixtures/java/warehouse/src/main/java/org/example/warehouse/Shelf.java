package org.example.warehouse;

import java.util.ArrayList;
import java.util.List;

/**
 * A shelf of one kind of product, up to a load in grams.
 */
public class Shelf<T extends Product> {
    private final String name;
    private final int loadGrams;
    private final List<T> products = new ArrayList<T>();
    private final List<Integer> pieces = new ArrayList<Integer>();

    public Shelf(String name, int loadGrams) {
        this.name = name;
        this.loadGrams = loadGrams;
    }

    public String getName() {
        return name;
    }

    /** Puts this many pieces of a product on the shelf, if it holds their weight. */
    public void put(T product, int count) throws ShelfFullException {
        int after = weightGrams() + product.weightGrams(count);
        if (after > loadGrams) {
            throw new ShelfFullException(name, after - loadGrams);
        }
        products.add(product);
        pieces.add(count);
    }

    public int weightGrams() {
        int total = 0;
        for (int i = 0; i < products.size(); i++) {
            total += products.get(i).weightGrams(pieces.get(i));
        }
        return total;
    }

    public int size() {
        return products.size();
    }

    /** The product at a position; IndexOutOfBoundsException past the end. */
    public T get(int index) {
        return products.get(index);
    }
}
