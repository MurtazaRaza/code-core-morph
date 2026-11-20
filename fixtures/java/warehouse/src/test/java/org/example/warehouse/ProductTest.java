package org.example.warehouse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.List;
import org.junit.Test;

public class ProductTest {
    private final Part bolt = new Part(new Sku("BO", 10), "Bolt", 150);
    private final Part nut = new Part(new Sku("NU", 10), "Nut", 40);

    @Test
    public void aPartWeighsItsPieces() {
        assertEquals(1500, bolt.weightGrams(10));
        assertEquals("BO-0010-6 Bolt x10 (1.5 kg)", bolt.label(10));
        assertFalse(bolt.isFreight(10));
        assertTrue(bolt.isFreight(201));
    }

    @Test(expected = IllegalArgumentException.class)
    public void aPartWeighsSomething() {
        new Part(new Sku("BO", 11), "Air", 0);
    }

    @Test
    public void aKitWeighsItsPartsAndItsBox() {
        Kit kit = new Kit(new Sku("KI", 1), "Fastening kit", 500).with(bolt).with(nut);
        assertEquals(2 * (500 + 150 + 40), kit.weightGrams(2));
        assertEquals("KI-0001-9 Fastening kit x2 (1.3 kg)", kit.label(2));
        List<Part> parts = kit.getParts();
        assertEquals(2, parts.size());
        assertEquals("Bolt", parts.get(0).getName());
    }

    @Test
    public void aKitOfManyPartsShipsAsFreight() {
        Kit kit = new Kit(new Sku("KI", 2), "Big kit", 10);
        for (int i = 0; i < 11; i++) {
            kit.with(new Part(new Sku("PA", i), "Part " + i, 1));
        }
        assertTrue(kit.isFreight(1));
        assertFalse(new Kit(new Sku("KI", 3), "Small kit", 10).with(nut).isFreight(1));
    }

    @Test
    public void aKitHoldsNoPartTwice() {
        Kit kit = new Kit(new Sku("KI", 4), "Kit", 10).with(bolt);
        try {
            kit.with(new Part(new Sku("BO", 10), "Another bolt", 1));
            fail("no exception");
        } catch (IllegalStateException e) {
            assertEquals("already in the kit: BO-0010-6", e.getMessage());
        }
    }

    @Test(expected = UnsupportedOperationException.class)
    public void aKitsPartsCantBeChanged() {
        new Kit(new Sku("KI", 5), "Kit", 10).getParts().add(bolt);
    }
}
