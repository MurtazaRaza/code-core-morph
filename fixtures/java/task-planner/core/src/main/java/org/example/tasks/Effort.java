package org.example.tasks;

/** How a task's estimate reads in a plan. */
public final class Effort {
    private Effort() {
    }

    public static String describe(int minutes) {
        String size;
        switch (minutes / 30) {
            case 0:
                size = "quick";
                break;
            case 1:
            case 2:
                size = "short";
                break;
            case 3:
            case 4:
            case 5:
            case 6:
            case 7:
                size = "long";
                break;
            default:
                size = "huge";
        }
        return size;
    }

    /** "1 h 30 min", "45 min", "2 h". */
    public static String format(int minutes) {
        int hours = minutes / 60;
        int rest = minutes % 60;
        if (hours == 0) {
            return rest + " min";
        }
        return rest == 0 ? hours + " h" : hours + " h " + rest + " min";
    }
}
