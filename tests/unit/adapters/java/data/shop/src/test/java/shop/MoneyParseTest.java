package shop;

import static org.junit.Assert.assertEquals;

import java.util.Arrays;
import java.util.Collection;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.Parameterized;
import org.junit.runners.Parameterized.Parameters;

@RunWith(Parameterized.class)
public class MoneyParseTest {
    @Parameters(name = "{0}")
    public static Collection<Object[]> cases() {
        return Arrays.asList(new Object[][] {
            {"12.34 EUR", 1234L},
            {"-0.05 USD", -5L},
            {"7 GBP", 700L},
        });
    }

    private final String text;
    private final long cents;

    public MoneyParseTest(String text, long cents) {
        this.text = text;
        this.cents = cents;
    }

    @Test
    public void parses() {
        assertEquals(cents, Money.parse(text).getCents());
    }
}
