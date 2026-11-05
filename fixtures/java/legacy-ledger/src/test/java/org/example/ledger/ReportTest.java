package org.example.ledger;

import static org.junit.Assert.assertEquals;

import java.io.StringReader;
import org.junit.Test;

public class ReportTest {
    private static Ledger ledger() throws Exception {
        Ledger ledger = new Ledger("EUR");
        ledger.load(new StringReader("2024-03-02,Groceries,-54.20,food\n2024-03-01,Salary,2500,income\n"
                + "2024-03-03,Bakery,-6.80,food\n2024-03-04,Flat,-900,rent\n"));
        return ledger;
    }

    @Test
    public void rendersEntriesOldestFirst() throws Exception {
        String expected = "2024-03-01  Salary               2500.00 EUR\n"
                + "2024-03-02  Groceries            -54.20 EUR\n"
                + "2024-03-03  Bakery               -6.80 EUR\n"
                + "2024-03-04  Flat                 -900.00 EUR\n"
                + "Balance: 1539.00 EUR\n";
        assertEquals(expected, new Report(ledger()).render());
    }

    @Test
    public void takesAnotherLineFormat() throws Exception {
        Report report = new Report(ledger());
        report.setLineFormat(new Report.LineFormat() {
            @Override
            public String format(String date, String description, Money amount) {
                return description + "=" + amount.getCents();
            }
        });
        assertEquals("Salary=250000\nGroceries=-5420\nBakery=-680\nFlat=-90000\nBalance: 1539.00 EUR\n",
                report.render());
    }

    @Test
    public void summarizesCategoriesByLabel() throws Exception {
        assertEquals("Food: 2, Income: 1, Rent: 1", new Report(ledger()).summary());
    }

    @Test
    public void labelsEveryCategory() {
        assertEquals("Income", Category.INCOME.label());
        assertEquals("Travel", Category.TRAVEL.label());
        assertEquals("Other", Category.of("gifts").label());
    }
}
