package org.example.ledger;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.io.StringReader;
import java.util.List;
import java.util.Map;
import org.junit.Before;
import org.junit.Test;

public class LedgerTest {
    private static final String CSV =
            "# date,description,amount,category\n"
                    + "2024-03-01,Salary,2500.00,income\n"
                    + "2024-02-28,Rent,-900.00,rent\n"
                    + "\n"
                    + "2024-03-02,Groceries,-54.20,food\n"
                    + "2024-03-02,Bakery,-6.80,food\n"
                    + "2023-12-24,Train,-42.00,travel\n";

    private Ledger ledger;

    @Before
    public void load() throws IOException {
        ledger = new Ledger("EUR");
        assertEquals(5, ledger.load(new StringReader(CSV)));
    }

    @Test
    public void keepsTheBalance() {
        assertEquals(new Money(149700, "EUR"), ledger.balance());
    }

    @Test
    public void sortsByDateKeepingTheOrderOfADay() {
        List<Entry> sorted = ledger.sorted();
        assertEquals("Train", sorted.get(0).getDescription());
        assertEquals("Rent", sorted.get(1).getDescription());
        assertEquals("Salary", sorted.get(2).getDescription());
        assertEquals("Groceries", sorted.get(3).getDescription());
        assertEquals("Bakery", sorted.get(4).getDescription());
    }

    @Test
    public void countsCategories() {
        Map<Category, Integer> counts = ledger.counts();
        assertEquals(Integer.valueOf(2), counts.get(Category.FOOD));
        assertEquals(Integer.valueOf(1), counts.get(Category.RENT));
        assertEquals(4, counts.size());
    }

    @Test
    public void totalsAYear() {
        Map<Category, Money> totals = ledger.totals(2024);
        assertEquals(new Money(-6100, "EUR"), totals.get(Category.FOOD));
        assertEquals(null, totals.get(Category.TRAVEL));
    }

    @Test
    public void readsDatesInUtc() {
        Entry train = ledger.sorted().get(0);
        assertEquals(12, train.month());
        assertEquals(2023, train.year());
    }

    @Test
    public void refusesABadLine() {
        try {
            new Ledger("EUR").load(new StringReader("2024-01-01,Coffee,-3.00\n"));
            fail("a line with three fields was read");
        } catch (IOException e) {
            assertEquals("line 1: expected 4 fields, got 3", e.getMessage());
        }
    }

    @Test
    public void refusesABadDate() {
        try {
            new Ledger("EUR").load(new StringReader("2024-02-30,Coffee,-3.00,food\n"));
            fail("February 30 was read");
        } catch (IOException e) {
            assertEquals("line 1: bad date 2024-02-30", e.getMessage());
        }
    }

    @Test(expected = IllegalArgumentException.class)
    public void keepsOneCurrency() {
        ledger.add(new Entry(new java.util.Date(0), "Dollars", new Money(1, "USD"), Category.OTHER));
    }
}
