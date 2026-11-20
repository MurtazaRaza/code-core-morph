package org.example.warehouse;

/**
 * Told of each change to a stock's count of a SKU.
 */
public interface StockListener {
    void changed(Sku sku, int before, int after);
}
