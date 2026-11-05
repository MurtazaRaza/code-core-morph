package org.example.tasks;

/** A task to plan: a title, a priority (1 is the most urgent) and an estimate in minutes. */
public final class Task {
    private final String title;
    private final int priority;
    private final int minutes;

    public Task(String title, int priority, int minutes) {
        if (priority < 1 || priority > 5) {
            throw new IllegalArgumentException("priority must be 1 to 5, not " + priority);
        }
        if (minutes <= 0) {
            throw new IllegalArgumentException("a task takes some time");
        }
        this.title = title;
        this.priority = priority;
        this.minutes = minutes;
    }

    public String getTitle() {
        return title;
    }

    public int getPriority() {
        return priority;
    }

    public int getMinutes() {
        return minutes;
    }

    @Override
    public boolean equals(Object o) {
        if (!(o instanceof Task)) {
            return false;
        }
        Task other = (Task) o;
        return title.equals(other.title) && priority == other.priority && minutes == other.minutes;
    }

    @Override
    public int hashCode() {
        return (title.hashCode() * 31 + priority) * 31 + minutes;
    }

    @Override
    public String toString() {
        return title + " (P" + priority + ", " + minutes + " min)";
    }
}
