package org.example.ledger;

import java.util.Calendar;
import java.util.Date;
import java.util.TimeZone;

/** One line of the ledger: when, what, and how much. */
public class Entry {
    private final Date date;
    private final String description;
    private final Money amount;
    private final Category category;

    public Entry(Date date, String description, Money amount, Category category) {
        this.date = new Date(date.getTime());
        this.description = description;
        this.amount = amount;
        this.category = category;
    }

    public Date getDate() {
        return new Date(date.getTime());
    }

    public String getDescription() {
        return description;
    }

    public Money getAmount() {
        return amount;
    }

    public Category getCategory() {
        return category;
    }

    /** The month of the entry, 1 to 12, in UTC. */
    public int month() {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        calendar.setTime(date);
        return calendar.get(Calendar.MONTH) + 1;
    }

    /** The year of the entry, in UTC. */
    public int year() {
        Calendar calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        calendar.setTime(date);
        return calendar.get(Calendar.YEAR);
    }
}
