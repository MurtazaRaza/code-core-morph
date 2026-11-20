package org.example.warehouse;

/**
 * A stock-keeping unit: a two-letter family, a number and a check digit, as in "AB-1234-7".
 * Immutable.
 */
public final class Sku implements Comparable<Sku> {
    private final String family;
    private final int number;

    public Sku(String family, int number) {
        if (family == null) {
            throw new NullPointerException("family");
        }
        if (family.length() != 2 || !Character.isUpperCase(family.charAt(0))
                || !Character.isUpperCase(family.charAt(1))) {
            throw new IllegalArgumentException("a family is two capital letters: " + family);
        }
        if (number < 0 || number > 9999) {
            throw new IllegalArgumentException("a number has four digits at most: " + number);
        }
        this.family = family;
        this.number = number;
    }

    /** Parses "AB-1234-7", checking the check digit. */
    public static Sku parse(String text) {
        String[] parts = text.trim().split("-");
        if (parts.length != 3) {
            throw new IllegalArgumentException("not a SKU: " + text);
        }
        int number = Integer.parseInt(parts[1]);
        Sku sku = new Sku(parts[0], number);
        if (sku.checkDigit() != Integer.parseInt(parts[2])) {
            throw new IllegalArgumentException("wrong check digit: " + text);
        }
        return sku;
    }

    public String getFamily() {
        return family;
    }

    public int getNumber() {
        return number;
    }

    /** The sum of the family's letters and the number's digits, modulo 10. */
    public int checkDigit() {
        int sum = family.charAt(0) + family.charAt(1);
        int n = number;
        while (n > 0) {
            sum += n % 10;
            n /= 10;
        }
        return sum % 10;
    }

    @Override
    public int compareTo(Sku other) {
        int byFamily = family.compareTo(other.family);
        return byFamily != 0 ? byFamily : Integer.compare(number, other.number);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (!(o instanceof Sku)) {
            return false;
        }
        Sku other = (Sku) o;
        return number == other.number && family.equals(other.family);
    }

    @Override
    public int hashCode() {
        return 31 * family.hashCode() + number;
    }

    @Override
    public String toString() {
        String digits = String.valueOf(number);
        while (digits.length() < 4) {
            digits = "0" + digits;
        }
        return family + "-" + digits + "-" + checkDigit();
    }
}
