package shop;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import org.junit.Before;
import org.junit.Test;

public class CartTest {
    private Cart cart;

    @Before
    public void setUp() {
        cart = new Cart("EUR");
        cart.add("bread", Category.FOOD, new Money(250, "EUR"), 2);
        cart.add("hammer", Category.TOOLS, new Money(1999, "EUR"), 1);
        cart.add("novel", Category.BOOKS, new Money(1200, "EUR"), 1);
    }

    @Test
    public void totalsItsItems() {
        assertEquals(new Money(3699, "EUR"), cart.total());
    }

    @Test
    public void keepsTheOrderOfItems() {
        cart.add("bread", Category.FOOD, new Money(250, "EUR"), 1);
        assertEquals(Arrays.asList("bread", "hammer", "novel"), cart.items());
        assertEquals(3, cart.quantity("bread"));
        assertEquals(0, cart.quantity("milk"));
    }

    @Test(expected = UnsupportedOperationException.class)
    public void itemsCantBeChanged() {
        List<String> items = cart.items();
        items.add("milk");
    }

    @Test
    public void appliesRulesInTurn() {
        final StringBuilder seen = new StringBuilder();
        cart.addRule(new PriceRule() {
            @Override
            public Money apply(String item, Category category, Money price) {
                seen.append(item).append(';');
                return category == Category.TOOLS ? price.percent(90) : price;
            }
        });
        cart.addRule(new PriceRule() {
            @Override
            public Money apply(String item, Category category, Money price) {
                return price.plus(new Money(-50, "EUR"));
            }
        });
        // 2 * 200 + (1799 - 50) + 1150
        assertEquals(new Money(3299, "EUR"), cart.total());
        assertEquals("bread;hammer;novel;", seen.toString());
    }

    @Test
    public void aRuleThatThrowsStopsTheTotal() {
        final IllegalStateException thrown = new IllegalStateException("closed");
        cart.addRule(new PriceRule() {
            @Override
            public Money apply(String item, Category category, Money price) {
                throw thrown;
            }
        });
        try {
            cart.total();
            fail("no exception");
        } catch (IllegalStateException e) {
            assertSame(thrown, e);
        }
    }

    @Test
    public void countsWhatAFilterAccepts() {
        assertEquals(3, cart.count((item, category) -> category != Category.TOOLS));
        assertEquals(1, cart.count((item, category) -> item.startsWith("h")));
    }

    @Test
    public void computesVat() {
        // 500 * 7% = 35, 1999 * 19% = 379.81 -> 380, 0
        assertEquals(new Money(415, "EUR"), cart.vat());
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesAnotherCurrency() {
        cart.add("milk", Category.FOOD, new Money(100, "USD"), 1);
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesNoQuantity() {
        cart.add("milk", Category.FOOD, new Money(100, "EUR"), 0);
    }

    @Test
    public void removes() {
        cart.remove("hammer");
        assertEquals(Arrays.asList("bread", "novel"), cart.items());
        try {
            cart.remove("hammer");
            fail("no exception");
        } catch (NoSuchElementException e) {
            assertEquals("not in the cart: hammer", e.getMessage());
        }
    }

    @Test
    public void checksOutFromTheStock() throws OutOfStockException {
        Map<String, Integer> stock = new HashMap<String, Integer>();
        stock.put("bread", 5);
        stock.put("hammer", 1);
        stock.put("novel", 3);
        cart.checkout(stock);
        assertEquals(Integer.valueOf(3), stock.get("bread"));
        assertEquals(Integer.valueOf(0), stock.get("hammer"));
        assertTrue(cart.items().isEmpty());
    }

    @Test
    public void refusesACheckoutTheStockCantServe() {
        Map<String, Integer> stock = new HashMap<String, Integer>();
        stock.put("bread", 1);
        stock.put("hammer", 1);
        try {
            cart.checkout(stock);
            fail("no exception");
        } catch (OutOfStockException e) {
            assertEquals("bread", e.getItem());
            assertEquals(1, e.getMissing());
            assertEquals("out of stock: bread (1 missing)", e.getMessage());
            assertEquals(Integer.valueOf(1), stock.get("bread"));
        }
    }

    @Test
    public void parsesCategories() {
        assertSame(Category.BOOKS, Category.parse(" books "));
        assertEquals(19, Category.parse("TOOLS").vatPercent());
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesUnknownCategories() {
        Category.parse("toys");
    }
}
