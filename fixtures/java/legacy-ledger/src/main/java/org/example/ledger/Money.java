package org.example.ledger;

/** An amount of money in one currency, in cents. Immutable. */
public final class Money {
    private final long cents;
    private final String currency;

    public Money(long cents, String currency) {
        if (currency == null || currency.length() != 3) {
            throw new IllegalArgumentException("currency must be a three-letter code: " + currency);
        }
        this.cents = cents;
        this.currency = currency;
    }

    /** "12.50 EUR", or "-3.05 USD". */
    public static Money parse(String text) {
        String[] parts = text.trim().split(" ");
        if (parts.length != 2) {
            throw new IllegalArgumentException("expected an amount and a currency: " + text);
        }
        String amount = parts[0];
        boolean negative = amount.startsWith("-");
        if (negative) {
            amount = amount.substring(1);
        }
        int dot = amount.indexOf('.');
        long whole = Long.parseLong(dot < 0 ? amount : amount.substring(0, dot));
        long fraction = dot < 0 ? 0 : Long.parseLong((amount.substring(dot + 1) + "00").substring(0, 2));
        long cents = whole * 100 + fraction;
        return new Money(negative ? -cents : cents, parts[1]);
    }

    public long getCents() {
        return cents;
    }

    public String getCurrency() {
        return currency;
    }

    public Money plus(Money other) {
        check(other);
        return new Money(cents + other.cents, currency);
    }

    public Money minus(Money other) {
        check(other);
        return new Money(cents - other.cents, currency);
    }

    public boolean isNegative() {
        return cents < 0;
    }

    private void check(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException("can't mix " + currency + " and " + other.currency);
        }
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof Money)) {
            return false;
        }
        Money other = (Money) o;
        return cents == other.cents && currency.equals(other.currency);
    }

    @Override
    public int hashCode() {
        return 31 * (int) (cents ^ (cents >>> 32)) + currency.hashCode();
    }

    @Override
    public String toString() {
        long abs = Math.abs(cents);
        String fraction = abs % 100 < 10 ? "0" + abs % 100 : String.valueOf(abs % 100);
        return (cents < 0 ? "-" : "") + abs / 100 + "." + fraction + " " + currency;
    }
}
