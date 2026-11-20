package shop;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;

/**
 * A shopping cart: items with their prices and quantities, and the rules that change prices.
 */
public class Cart {
    private final String currency;
    private final Map<String, Integer> quantities = new LinkedHashMap<String, Integer>();
    private final Map<String, Money> prices = new HashMap<String, Money>();
    private final Map<String, Category> categories = new HashMap<String, Category>();
    private final List<PriceRule> rules = new ArrayList<PriceRule>();

    public Cart(String currency) {
        this.currency = currency;
    }

    public String getCurrency() {
        return currency;
    }

    /** Adds a quantity of an item; its price must be in the cart's currency. */
    public void add(String item, Category category, Money price, int quantity) {
        if (item == null) {
            throw new NullPointerException("item");
        }
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive: " + quantity);
        }
        if (!price.getCurrency().equals(currency)) {
            throw new IllegalArgumentException("price in " + price.getCurrency() + ", cart in " + currency);
        }
        Integer had = quantities.get(item);
        quantities.put(item, had == null ? quantity : had + quantity);
        prices.put(item, price);
        categories.put(item, category);
    }

    public void remove(String item) {
        if (quantities.remove(item) == null) {
            throw new NoSuchElementException("not in the cart: " + item);
        }
        prices.remove(item);
        categories.remove(item);
    }

    public int quantity(String item) {
        Integer q = quantities.get(item);
        return q == null ? 0 : q;
    }

    /** The items, in the order they were first added. */
    public List<String> items() {
        return Collections.unmodifiableList(new ArrayList<String>(quantities.keySet()));
    }

    public void addRule(PriceRule rule) {
        rules.add(rule);
    }

    /** The cart's total, each item's price through every rule in turn, then times its quantity. */
    public Money total() {
        Money sum = new Money(0, currency);
        for (Map.Entry<String, Integer> e : quantities.entrySet()) {
            String item = e.getKey();
            Money price = prices.get(item);
            for (PriceRule rule : rules) {
                price = rule.apply(item, categories.get(item), price);
            }
            sum = sum.plus(price.times(e.getValue()));
        }
        return sum;
    }

    /** The VAT the total includes, per category's rate, rounded per item. */
    public Money vat() {
        Money sum = new Money(0, currency);
        for (Map.Entry<String, Integer> e : quantities.entrySet()) {
            Category c = categories.get(e.getKey());
            sum = sum.plus(prices.get(e.getKey()).times(e.getValue()).percent(c.vatPercent()));
        }
        return sum;
    }

    /** How many units of the items the filter accepts. */
    public int count(ItemFilter filter) {
        int n = 0;
        for (Map.Entry<String, Integer> e : quantities.entrySet()) {
            if (filter.accepts(e.getKey(), categories.get(e.getKey()))) {
                n += e.getValue();
            }
        }
        return n;
    }

    /** Takes the cart's items from the stock, or none of them if one is short. */
    public void checkout(Map<String, Integer> stock) throws OutOfStockException {
        for (Map.Entry<String, Integer> e : quantities.entrySet()) {
            Integer have = stock.get(e.getKey());
            int has = have == null ? 0 : have;
            if (has < e.getValue()) {
                throw new OutOfStockException(e.getKey(), e.getValue() - has);
            }
        }
        for (Map.Entry<String, Integer> e : quantities.entrySet()) {
            stock.put(e.getKey(), stock.get(e.getKey()) - e.getValue());
        }
        quantities.clear();
        prices.clear();
        categories.clear();
    }

    /** Accepts or rejects an item. */
    public interface ItemFilter {
        boolean accepts(String item, Category category);
    }
}
