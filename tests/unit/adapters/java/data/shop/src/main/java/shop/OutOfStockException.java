package shop;

/**
 * Thrown when a checkout asks for more than the stock has.
 */
public class OutOfStockException extends Exception {
    private static final long serialVersionUID = 1L;

    private final String item;
    private final int missing;

    public OutOfStockException(String item, int missing) {
        super("out of stock: " + item + " (" + missing + " missing)");
        this.item = item;
        this.missing = missing;
    }

    public String getItem() {
        return item;
    }

    public int getMissing() {
        return missing;
    }
}
