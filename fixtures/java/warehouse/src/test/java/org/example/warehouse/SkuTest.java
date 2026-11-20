package org.example.warehouse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashSet;
import java.util.Set;
import org.junit.Test;

public class SkuTest {
    @Test
    public void printsWithItsCheckDigit() {
        Sku sku = new Sku("AB", 1234);
        // 'A' + 'B' = 131, + 1 + 2 + 3 + 4 = 141
        assertEquals(1, sku.checkDigit());
        assertEquals("AB-1234-1", sku.toString());
        assertEquals("AB-0007-8", new Sku("AB", 7).toString());
    }

    @Test
    public void parsesWhatItPrints() {
        Sku sku = Sku.parse(" AB-1234-1 ");
        assertEquals(new Sku("AB", 1234), sku);
        assertEquals("AB", sku.getFamily());
        assertEquals(1234, sku.getNumber());
    }

    @Test
    public void refusesAWrongCheckDigit() {
        try {
            Sku.parse("AB-1234-2");
            fail("no exception");
        } catch (IllegalArgumentException e) {
            assertEquals("wrong check digit: AB-1234-2", e.getMessage());
        }
    }

    @Test(expected = NumberFormatException.class)
    public void refusesANumberThatIsntOne() {
        Sku.parse("AB-12x4-1");
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesALowerCaseFamily() {
        new Sku("ab", 1);
    }

    @Test(expected = NullPointerException.class)
    public void needsAFamily() {
        new Sku(null, 1);
    }

    @Test
    public void equalSkusHashAlike() {
        Set<Sku> set = new HashSet<Sku>();
        set.add(new Sku("AB", 1));
        set.add(new Sku("AB", 1));
        set.add(new Sku("AC", 1));
        assertEquals(2, set.size());
        assertEquals(new Sku("AB", 1).hashCode(), new Sku("AB", 1).hashCode());
        assertNotEquals(new Sku("AB", 1), "AB-0001-2");
    }

    @Test
    public void ordersByFamilyThenNumber() {
        assertTrue(new Sku("AB", 9).compareTo(new Sku("AC", 1)) < 0);
        assertTrue(new Sku("AB", 2).compareTo(new Sku("AB", 10)) < 0);
        assertEquals(0, new Sku("AB", 2).compareTo(new Sku("AB", 2)));
    }
}
