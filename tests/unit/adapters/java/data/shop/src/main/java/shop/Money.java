package shop;

/**
 * An amount of money in cents, with its currency. Immutable.
 */
public final class Money implements Comparable<Money> {
    private final long cents;
    private final String currency;

    public Money(long cents, String currency) {
        if (currency == null) {
            throw new NullPointerException("currency");
        }
        if (currency.length() != 3) {
            throw new IllegalArgumentException("a currency has three letters: " + currency);
        }
        this.cents = cents;
        this.currency = currency;
    }

    /** Parses "12.34 EUR". */
    public static Money parse(String text) {
        String[] parts = text.trim().split(" ");
        if (parts.length != 2) {
            throw new IllegalArgumentException("not an amount: " + text);
        }
        String number = parts[0];
        boolean negative = number.startsWith("-");
        if (negative) {
            number = number.substring(1);
        }
        int dot = number.indexOf('.');
        long whole = Long.parseLong(dot < 0 ? number : number.substring(0, dot));
        long frac = 0;
        if (dot >= 0) {
            String f = number.substring(dot + 1);
            if (f.length() != 2) {
                throw new NumberFormatException("two decimals: " + text);
            }
            frac = Long.parseLong(f);
        }
        long cents = whole * 100 + frac;
        return new Money(negative ? -cents : cents, parts[1]);
    }

    public long getCents() {
        return cents;
    }

    public String getCurrency() {
        return currency;
    }

    public Money plus(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException("currencies differ: " + currency + " and " + other.currency);
        }
        return new Money(cents + other.cents, currency);
    }

    public Money times(int n) {
        return new Money(cents * n, currency);
    }

    /** This amount's percentage, rounded half up to the cent. */
    public Money percent(int percent) {
        long scaled = cents * percent;
        long rounded = scaled >= 0 ? (scaled + 50) / 100 : -((-scaled + 50) / 100);
        return new Money(rounded, currency);
    }

    public boolean isZero() {
        return cents == 0;
    }

    @Override
    public int compareTo(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException("currencies differ: " + currency + " and " + other.currency);
        }
        return cents < other.cents ? -1 : (cents == other.cents ? 0 : 1);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof Money)) {
            return false;
        }
        Money m = (Money) o;
        return cents == m.cents && currency.equals(m.currency);
    }

    @Override
    public int hashCode() {
        int h = 17;
        h = 31 * h + (int) (cents ^ (cents >>> 32));
        h = 31 * h + currency.hashCode();
        return h;
    }

    @Override
    public String toString() {
        long abs = Math.abs(cents);
        String frac = String.valueOf(abs % 100);
        if (frac.length() < 2) {
            frac = "0" + frac;
        }
        return (cents < 0 ? "-" : "") + (abs / 100) + "." + frac + " " + currency;
    }
}
