package shop;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashSet;
import java.util.Set;
import org.junit.Test;

public class MoneyTest {
    @Test
    public void addsInOneCurrency() {
        Money a = new Money(1050, "EUR");
        Money b = new Money(250, "EUR");
        assertEquals(new Money(1300, "EUR"), a.plus(b));
        assertEquals("13.00 EUR", a.plus(b).toString());
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesTwoCurrencies() {
        new Money(1, "EUR").plus(new Money(1, "USD"));
    }

    @Test
    public void namesTheCurrenciesThatDiffer() {
        try {
            new Money(1, "EUR").plus(new Money(1, "USD"));
            fail("no exception");
        } catch (IllegalArgumentException e) {
            assertEquals("currencies differ: EUR and USD", e.getMessage());
        }
    }

    @Test(expected = NullPointerException.class)
    public void needsACurrency() {
        new Money(1, null);
    }

    @Test
    public void roundsPercentagesHalfUp() {
        assertEquals(new Money(75, "EUR"), new Money(1071, "EUR").percent(7));
        assertEquals(new Money(-75, "EUR"), new Money(-1071, "EUR").percent(7));
        assertEquals(new Money(0, "EUR"), new Money(7, "EUR").percent(7));
    }

    @Test
    public void equalAmountsHashAlike() {
        Set<Money> set = new HashSet<Money>();
        set.add(new Money(5, "EUR"));
        set.add(new Money(5, "EUR"));
        set.add(new Money(5, "USD"));
        assertEquals(2, set.size());
        assertEquals(new Money(5, "EUR").hashCode(), new Money(5, "EUR").hashCode());
        // the hash wraps around, as Java's int arithmetic does
        assertEquals(-2147398285, new Money(Long.MAX_VALUE, "EUR").hashCode());
        assertNotEquals(new Money(5, "EUR"), "5 EUR");
    }

    @Test
    public void compares() {
        assertTrue(new Money(1, "EUR").compareTo(new Money(2, "EUR")) < 0);
        assertEquals(0, new Money(2, "EUR").compareTo(new Money(2, "EUR")));
        assertFalse(new Money(3, "EUR").isZero());
        assertTrue(new Money(0, "EUR").isZero());
    }

    @Test
    public void overflowWraps() {
        assertEquals(Long.MIN_VALUE, new Money(Long.MAX_VALUE, "EUR").plus(new Money(1, "EUR")).getCents());
    }

    @Test(expected = NumberFormatException.class)
    public void needsTwoDecimals() {
        Money.parse("1.234 EUR");
    }

    @Test
    public void printsNegativeAmounts() {
        assertEquals("-0.05 EUR", new Money(-5, "EUR").toString());
        assertEquals("1.20 ÄÖÜ", new Money(120, "ÄÖÜ").toString());
    }
}
