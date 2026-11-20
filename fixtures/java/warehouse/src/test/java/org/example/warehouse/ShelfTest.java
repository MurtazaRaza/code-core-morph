package org.example.warehouse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.fail;

import org.junit.Test;

public class ShelfTest {
    private final Part bolt = new Part(new Sku("BO", 10), "Bolt", 150);

    @Test
    public void holdsWhatItsLoadAllows() throws ShelfFullException {
        Shelf<Part> shelf = new Shelf<Part>("A1", 5000);
        shelf.put(bolt, 20);
        assertEquals(3000, shelf.weightGrams());
        assertEquals(1, shelf.size());
        assertSame(bolt, shelf.get(0));
        assertEquals("A1", shelf.getName());
    }

    @Test
    public void refusesWhatIsTooHeavy() throws ShelfFullException {
        Shelf<Part> shelf = new Shelf<Part>("A2", 5000);
        shelf.put(bolt, 30);
        try {
            shelf.put(bolt, 5);
            fail("no exception");
        } catch (ShelfFullException e) {
            assertEquals(250, e.getOverGrams());
            assertEquals("shelf A2 is full: 250 g over", e.getMessage());
            assertEquals(1, shelf.size());
        }
    }

    @Test
    public void holdsKitsToo() throws ShelfFullException {
        Shelf<Kit> shelf = new Shelf<Kit>("B1", 100000);
        Kit kit = new Kit(new Sku("KI", 1), "Kit", 500).with(bolt);
        shelf.put(kit, 3);
        assertEquals(1950, shelf.weightGrams());
    }

    @Test(expected = IndexOutOfBoundsException.class)
    public void hasNothingPastTheEnd() {
        new Shelf<Part>("C1", 10).get(0);
    }
}
