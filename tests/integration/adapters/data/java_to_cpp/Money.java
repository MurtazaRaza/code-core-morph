package shop;

import codecoremorph.interop.CcmInterop;
import codecoremorph.interop.CcmNative;

/**
 * An amount of money in cents, with its currency. Immutable.
 */
public final class Money implements Comparable<Money> {
    private final long cents;
    private final String currency;

    public Money(long cents, String currency) {
        CcmInterop.check(CcmNative.shop_Money_new(cents, currency));
        this.cents = cents;
        this.currency = currency;
    }

    // The fields of a Money the native code made, checked there.
    private Money(long cents, String currency, boolean checked) {
        this.cents = cents;
        this.currency = currency;
    }

    static Money of(long cents, String currency) {
        return new Money(cents, currency, true);
    }

    /** Parses "12.34 EUR". */
    public static Money parse(String text) {
        long[] cents = new long[1];
        String[] currency = new String[1];
        CcmInterop.check(CcmNative.shop_Money_parse(text, cents, currency));
        return of(cents[0], currency[0]);
    }

    public long getCents() {
        return cents;
    }

    public String getCurrency() {
        return currency;
    }

    public Money plus(Money other) {
        long[] cents = new long[1];
        String[] currency = new String[1];
        CcmInterop.check(CcmNative.shop_Money_plus(this.cents, this.currency, other.cents,
            other.currency, cents, currency));
        return of(cents[0], currency[0]);
    }

    public Money times(int n) {
        long[] cents = new long[1];
        CcmInterop.check(CcmNative.shop_Money_times(this.cents, currency, n, cents));
        return of(cents[0], currency);
    }

    /** This amount's percentage, rounded half up to the cent. */
    public Money percent(int percent) {
        long[] cents = new long[1];
        CcmInterop.check(CcmNative.shop_Money_percent(this.cents, currency, percent, cents));
        return of(cents[0], currency);
    }

    public boolean isZero() {
        return cents == 0;
    }

    @Override
    public int compareTo(Money other) {
        int[] result = new int[1];
        CcmInterop.check(CcmNative.shop_Money_compareTo(cents, currency, other.cents,
            other.currency, result));
        return result[0];
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
        int[] result = new int[1];
        CcmInterop.check(CcmNative.shop_Money_hashCode(cents, currency, result));
        return result[0];
    }

    @Override
    public String toString() {
        String[] result = new String[1];
        CcmInterop.check(CcmNative.shop_Money_toString(cents, currency, result));
        return result[0];
    }
}
