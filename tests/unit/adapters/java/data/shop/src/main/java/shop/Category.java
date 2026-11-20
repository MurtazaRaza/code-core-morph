package shop;

/**
 * What an item is, with the VAT rate it pays.
 */
public enum Category {
    FOOD(7),
    BOOKS(0),
    TOOLS(19);

    private final int vatPercent;

    Category(int vatPercent) {
        this.vatPercent = vatPercent;
    }

    /** The VAT rate, in percent. */
    public int vatPercent() {
        return vatPercent;
    }

    /** The category named, in any case; IllegalArgumentException for none. */
    public static Category parse(String text) {
        if (text == null) {
            throw new NullPointerException("text");
        }
        for (Category c : values()) {
            if (c.name().equalsIgnoreCase(text.trim())) {
                return c;
            }
        }
        throw new IllegalArgumentException("no category " + text);
    }
}
