package org.example.ledger;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.Reader;
import java.text.ParseException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/** The entries of one account, in one currency. */
public class Ledger {
    private final String currency;
    private final List entries = new ArrayList();

    public Ledger(String currency) {
        this.currency = currency;
    }

    public void add(Entry entry) {
        if (!entry.getAmount().getCurrency().equals(currency)) {
            throw new IllegalArgumentException("this ledger keeps " + currency);
        }
        entries.add(entry);
    }

    public int size() {
        return entries.size();
    }

    /** The entries, oldest first; entries of the same day keep the order they were added in. */
    public List<Entry> sorted() {
        List<Entry> copy = new ArrayList<Entry>(entries);
        Collections.sort(copy, new Comparator<Entry>() {
            @Override
            public int compare(Entry a, Entry b) {
                return a.getDate().compareTo(b.getDate());
            }
        });
        return copy;
    }

    public Money balance() {
        Money total = new Money(0, currency);
        for (Iterator it = entries.iterator(); it.hasNext(); ) {
            Entry entry = (Entry) it.next();
            total = total.plus(entry.getAmount());
        }
        return total;
    }

    /** How many entries each category has. */
    public Map<Category, Integer> counts() {
        Map<Category, Integer> counts = new HashMap<Category, Integer>();
        for (Object o : entries) {
            Entry entry = (Entry) o;
            Integer old = counts.get(entry.getCategory());
            counts.put(entry.getCategory(), old == null ? new Integer(1) : new Integer(old.intValue() + 1));
        }
        return counts;
    }

    /** The total of each category, for the year given. */
    public Map<Category, Money> totals(int year) {
        Map<Category, Money> totals = new HashMap<Category, Money>();
        for (Object o : entries) {
            Entry entry = (Entry) o;
            if (entry.year() != year) {
                continue;
            }
            Money old = totals.get(entry.getCategory());
            totals.put(entry.getCategory(), old == null ? entry.getAmount() : old.plus(entry.getAmount()));
        }
        return totals;
    }

    /**
     * Reads entries from CSV lines {@code yyyy-MM-dd,description,amount,category}; blank
     * lines and lines starting with {@code #} are skipped. Returns how many it read.
     */
    public int load(Reader source) throws IOException {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd");
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        format.setLenient(false);
        BufferedReader reader = new BufferedReader(source);
        int read = 0;
        try {
            String line;
            int number = 0;
            while ((line = reader.readLine()) != null) {
                number++;
                if (line.trim().length() == 0 || line.startsWith("#")) {
                    continue;
                }
                String[] fields = line.split(",");
                if (fields.length != 4) {
                    throw new IOException("line " + number + ": expected 4 fields, got " + fields.length);
                }
                Date date;
                try {
                    date = format.parse(fields[0]);
                } catch (ParseException e) {
                    throw new IOException("line " + number + ": bad date " + fields[0]);
                }
                add(new Entry(date, fields[1], Money.parse(fields[2] + " " + currency), Category.of(fields[3])));
                read++;
            }
        } finally {
            reader.close();
        }
        return read;
    }
}
