package org.example.ledger;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;

/** A plain-text report of a ledger. */
public class Report {
    /** How one line of the report is written. */
    public interface LineFormat {
        String format(String date, String description, Money amount);
    }

    private final Ledger ledger;
    private LineFormat lineFormat = new LineFormat() {
        @Override
        public String format(String date, String description, Money amount) {
            return date + "  " + pad(description, 20) + " " + amount;
        }
    };

    public Report(Ledger ledger) {
        this.ledger = ledger;
    }

    public void setLineFormat(LineFormat lineFormat) {
        this.lineFormat = lineFormat;
    }

    public String render() {
        SimpleDateFormat format = new SimpleDateFormat("yyyy-MM-dd");
        format.setTimeZone(TimeZone.getTimeZone("UTC"));
        StringBuffer out = new StringBuffer();
        for (Entry entry : ledger.sorted()) {
            out.append(lineFormat.format(format.format(entry.getDate()), entry.getDescription(), entry.getAmount()));
            out.append('\n');
        }
        out.append("Balance: ").append(ledger.balance()).append('\n');
        return out.toString();
    }

    /** "Food: 2, Rent: 1", the categories in label order. */
    public String summary() {
        Map<Category, Integer> counts = ledger.counts();
        List<Category> categories = new ArrayList<Category>(counts.keySet());
        Collections.sort(categories, new Comparator<Category>() {
            @Override
            public int compare(Category a, Category b) {
                return a.label().compareTo(b.label());
            }
        });
        StringBuffer out = new StringBuffer();
        for (Category category : categories) {
            if (out.length() > 0) {
                out.append(", ");
            }
            out.append(category.label()).append(": ").append(counts.get(category));
        }
        return out.toString();
    }

    static String pad(String text, int width) {
        StringBuffer out = new StringBuffer(text);
        while (out.length() < width) {
            out.append(' ');
        }
        return out.toString();
    }
}
