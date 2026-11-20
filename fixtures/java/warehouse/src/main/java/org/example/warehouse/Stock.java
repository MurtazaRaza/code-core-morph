package org.example.warehouse;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;

/**
 * Counts of the warehouse's SKUs, in the order they were first stocked, and the listeners told
 * of each change. Safe to share between threads.
 */
public class Stock {
    private final Map<Sku, Integer> counts = new LinkedHashMap<Sku, Integer>();
    private final List<StockListener> listeners = new ArrayList<StockListener>();

    public synchronized void addListener(StockListener listener) {
        listeners.add(listener);
    }

    /** Receives a number of packs of a SKU. */
    public void receive(Sku sku, Packing packing, int packs) {
        if (packs <= 0) {
            throw new IllegalArgumentException("receive at least one pack: " + packs);
        }
        change(sku, packing.toPieces(packs));
    }

    /** Takes pieces out; NoSuchElementException for a SKU never stocked. */
    public void take(Sku sku, int pieces) {
        int have;
        synchronized (this) {
            Integer count = counts.get(sku);
            if (count == null) {
                throw new NoSuchElementException("never stocked: " + sku);
            }
            have = count;
        }
        if (pieces > have) {
            throw new IllegalStateException("only " + have + " of " + sku + " in stock");
        }
        change(sku, -pieces);
    }

    public synchronized int count(Sku sku) {
        Integer count = counts.get(sku);
        return count == null ? 0 : count;
    }

    /** How many pieces of the SKUs the filter accepts are in stock. */
    public synchronized int total(SkuFilter filter) {
        int total = 0;
        for (Map.Entry<Sku, Integer> e : counts.entrySet()) {
            if (filter.accepts(e.getKey())) {
                total += e.getValue();
            }
        }
        return total;
    }

    /** One line per SKU, in the order they were first stocked: "AB-1234-7: 12". */
    public synchronized String report() {
        StringBuilder out = new StringBuilder();
        for (Map.Entry<Sku, Integer> e : counts.entrySet()) {
            out.append(e.getKey()).append(": ").append(e.getValue()).append('\n');
        }
        return out.toString();
    }

    private void change(Sku sku, int delta) {
        int before;
        int after;
        List<StockListener> told;
        synchronized (this) {
            Integer count = counts.get(sku);
            before = count == null ? 0 : count;
            after = before + delta;
            counts.put(sku, after);
            told = new ArrayList<StockListener>(listeners);
        }
        for (StockListener listener : told) {
            listener.changed(sku, before, after);
        }
    }

    /** Accepts or rejects a SKU. */
    public interface SkuFilter {
        boolean accepts(Sku sku);
    }
}
