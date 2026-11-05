package org.example.ledger;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class MoneyTest {
    @Test
    public void parsesAmounts() {
        assertEquals(1250, Money.parse("12.50 EUR").getCents());
        assertEquals(-305, Money.parse("-3.05 USD").getCents());
        assertEquals(700, Money.parse("7 EUR").getCents());
        assertEquals(1210, Money.parse("12.1 EUR").getCents());
    }

    @Test
    public void printsAmounts() {
        assertEquals("12.50 EUR", new Money(1250, "EUR").toString());
        assertEquals("-3.05 USD", new Money(-305, "USD").toString());
        assertEquals("0.00 EUR", new Money(0, "EUR").toString());
    }

    @Test
    public void addsAndSubtracts() {
        Money a = new Money(1000, "EUR");
        Money b = new Money(250, "EUR");
        assertEquals(new Money(1250, "EUR"), a.plus(b));
        assertEquals(new Money(750, "EUR"), a.minus(b));
        assertTrue(b.minus(a).isNegative());
        assertFalse(a.isNegative());
    }

    @Test
    public void equalAmountsAreEqual() {
        assertEquals(new Money(5, "EUR"), new Money(5, "EUR"));
        assertEquals(new Money(5, "EUR").hashCode(), new Money(5, "EUR").hashCode());
        assertFalse(new Money(5, "EUR").equals(new Money(5, "USD")));
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesToMixCurrencies() {
        new Money(1, "EUR").plus(new Money(1, "USD"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesABadCurrency() {
        new Money(1, "EURO");
    }
}
