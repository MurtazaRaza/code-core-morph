package org.example.warehouse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.fail;

import java.util.ArrayList;
import java.util.List;
import java.util.NoSuchElementException;
import org.junit.Before;
import org.junit.Test;

public class StockTest {
    private final Sku bolts = new Sku("BO", 10);
    private final Sku nuts = new Sku("NU", 10);
    private Stock stock;

    @Before
    public void setUp() {
        stock = new Stock();
        stock.receive(bolts, Packing.DOZEN, 2);
        stock.receive(nuts, Packing.CRATE, 1);
    }

    @Test
    public void countsWhatItReceives() {
        assertEquals(24, stock.count(bolts));
        assertEquals(48, stock.count(nuts));
        assertEquals(0, stock.count(new Sku("XX", 1)));
    }

    @Test
    public void takesWhatItHas() {
        stock.take(bolts, 20);
        assertEquals(4, stock.count(bolts));
    }

    @Test
    public void refusesToTakeMoreThanItHas() {
        try {
            stock.take(bolts, 25);
            fail("no exception");
        } catch (IllegalStateException e) {
            assertEquals("only 24 of BO-0010-6 in stock", e.getMessage());
        }
    }

    @Test(expected = NoSuchElementException.class)
    public void refusesToTakeWhatItNeverHad() {
        stock.take(new Sku("XX", 1), 1);
    }

    @Test(expected = IllegalArgumentException.class)
    public void receivesAtLeastOnePack() {
        stock.receive(bolts, Packing.PIECE, 0);
    }

    @Test
    public void tellsItsListeners() {
        final List<String> heard = new ArrayList<String>();
        stock.addListener(new StockListener() {
            @Override
            public void changed(Sku sku, int before, int after) {
                heard.add(sku + " " + before + "->" + after);
            }
        });
        stock.take(nuts, 8);
        stock.receive(bolts, Packing.PIECE, 1);
        assertEquals(2, heard.size());
        assertEquals("NU-0010-4 48->40", heard.get(0));
        assertEquals("BO-0010-6 24->25", heard.get(1));
    }

    @Test
    public void aListenerThatThrowsStopsTheChangeReport() {
        final RuntimeException thrown = new RuntimeException("listener broke");
        stock.addListener(new StockListener() {
            @Override
            public void changed(Sku sku, int before, int after) {
                throw thrown;
            }
        });
        try {
            stock.take(nuts, 1);
            fail("no exception");
        } catch (RuntimeException e) {
            assertSame(thrown, e);
        }
        assertEquals(47, stock.count(nuts));
    }

    @Test
    public void totalsWhatAFilterAccepts() {
        assertEquals(72, stock.total(sku -> true));
        assertEquals(24, stock.total(sku -> sku.getFamily().equals("BO")));
    }

    @Test
    public void reportsInTheOrderItStocked() {
        stock.receive(new Sku("AA", 1), Packing.PIECE, 5);
        assertEquals("BO-0010-6: 24\nNU-0010-4: 48\nAA-0001-1: 5\n", stock.report());
    }
}
