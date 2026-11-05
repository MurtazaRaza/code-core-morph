package org.example.ledger;

public enum Category {
    INCOME,
    RENT,
    FOOD,
    TRAVEL,
    OTHER;

    /** The label a report prints. */
    public String label() {
        String label;
        switch (this) {
            case INCOME:
                label = "Income";
                break;
            case RENT:
                label = "Rent";
                break;
            case FOOD:
                label = "Food";
                break;
            case TRAVEL:
                label = "Travel";
                break;
            default:
                label = "Other";
        }
        return label;
    }

    /** The category a CSV file names, OTHER when it names none of them. */
    public static Category of(String name) {
        for (Category category : values()) {
            if (category.name().equalsIgnoreCase(name.trim())) {
                return category;
            }
        }
        return OTHER;
    }
}
