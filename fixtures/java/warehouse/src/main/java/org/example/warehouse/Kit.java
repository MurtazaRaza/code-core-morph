package org.example.warehouse;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Parts sold together, with the box they come in.
 */
public class Kit extends Product {
    private final List<Part> parts = new ArrayList<Part>();
    private final int boxGrams;

    public Kit(Sku sku, String name, int boxGrams) {
        super(sku, name);
        this.boxGrams = boxGrams;
    }

    /** Adds a part; a kit holds no part twice. */
    public Kit with(Part part) {
        for (Part p : parts) {
            if (p.getSku().equals(part.getSku())) {
                throw new IllegalStateException("already in the kit: " + part.getSku());
            }
        }
        parts.add(part);
        return this;
    }

    public List<Part> getParts() {
        return Collections.unmodifiableList(parts);
    }

    @Override
    public int weightGrams(int pieces) {
        int each = boxGrams;
        for (Part p : parts) {
            each += p.weightGrams(1);
        }
        return pieces * each;
    }

    @Override
    public boolean isFreight(int pieces) {
        // A kit of more than ten parts always ships as freight.
        return parts.size() > 10 || super.isFreight(pieces);
    }
}
