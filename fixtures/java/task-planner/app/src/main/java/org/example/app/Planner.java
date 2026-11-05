package org.example.app;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.List;
import java.util.TimeZone;
import javax.annotation.PostConstruct;
import org.example.tasks.Effort;
import org.example.tasks.Task;
import org.example.tasks.TaskQueue;

/** Plans a working day from a queue of tasks. */
public class Planner {
    private final TaskQueue queue = new TaskQueue();
    private int dayMinutes;
    private boolean ready;

    /** Called by the container after construction; tests call it themselves. */
    @PostConstruct
    public void init() {
        dayMinutes = 8 * 60;
        ready = true;
    }

    public void add(String title, int priority, int minutes) {
        queue.add(new Task(title, priority, minutes));
    }

    public void setDayMinutes(int dayMinutes) {
        this.dayMinutes = dayMinutes;
    }

    /** The day's plan, starting at 09:00 UTC on the date given. */
    public String plan(Date day) {
        if (!ready) {
            throw new IllegalStateException("init() wasn't called");
        }
        Calendar clock = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        clock.setTime(day);
        clock.set(Calendar.HOUR_OF_DAY, 9);
        clock.set(Calendar.MINUTE, 0);
        clock.set(Calendar.SECOND, 0);
        clock.set(Calendar.MILLISECOND, 0);
        SimpleDateFormat time = new SimpleDateFormat("HH:mm");
        time.setTimeZone(TimeZone.getTimeZone("UTC"));
        List<Task> tasks = queue.fill(dayMinutes);
        StringWriter text = new StringWriter();
        PrintWriter out = new PrintWriter(text);
        try {
            for (Task task : tasks) {
                out.printf("%s %-24s %s, %s%n", time.format(clock.getTime()), task.getTitle(),
                        Effort.format(task.getMinutes()), Effort.describe(task.getMinutes()));
                clock.add(Calendar.MINUTE, task.getMinutes());
            }
            out.printf("Left for tomorrow: %d task(s)%n", queue.size());
        } finally {
            out.close();
        }
        return text.toString();
    }

    public static String priorityName(int priority) {
        switch (priority) {
            case 1:
                return "urgent";
            case 2:
                return "high";
            case 3:
                return "normal";
            default:
                return "low";
        }
    }
}
