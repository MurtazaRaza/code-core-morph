package shop;

/**
 * A rule that changes an item's price in a cart.
 */
public interface PriceRule {
    /** The price after the rule, given the price before it. */
    Money apply(String item, Category category, Money price);
}
