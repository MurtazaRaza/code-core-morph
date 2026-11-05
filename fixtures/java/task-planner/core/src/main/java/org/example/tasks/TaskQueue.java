package org.example.tasks;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.Iterator;
import java.util.List;
import java.util.PriorityQueue;

/** Tasks by priority, then by estimate, then in the order they were added. */
public class TaskQueue {
    private static final class Slot {
        final Task task;
        final long order;

        Slot(Task task, long order) {
            this.task = task;
            this.order = order;
        }
    }

    private final PriorityQueue<Slot> slots = new PriorityQueue<Slot>(11, new Comparator<Slot>() {
        @Override
        public int compare(Slot a, Slot b) {
            if (a.task.getPriority() != b.task.getPriority()) {
                return a.task.getPriority() - b.task.getPriority();
            }
            if (a.task.getMinutes() != b.task.getMinutes()) {
                return a.task.getMinutes() - b.task.getMinutes();
            }
            return Long.compare(a.order, b.order);
        }
    });
    private long added;

    public synchronized void add(Task task) {
        slots.add(new Slot(task, added++));
    }

    public synchronized Task next() {
        Slot slot = slots.poll();
        return slot == null ? null : slot.task;
    }

    public synchronized int size() {
        return slots.size();
    }

    /** Takes tasks, most urgent first, until their estimates fill the minutes given. */
    public synchronized List<Task> fill(int minutes) {
        List<Task> taken = new ArrayList<Task>();
        List<Slot> skipped = new ArrayList<Slot>();
        int left = minutes;
        while (!slots.isEmpty()) {
            Slot slot = slots.poll();
            if (slot.task.getMinutes() <= left) {
                taken.add(slot.task);
                left -= slot.task.getMinutes();
            } else {
                skipped.add(slot);
            }
        }
        for (Iterator it = skipped.iterator(); it.hasNext(); ) {
            slots.add((Slot) it.next());
        }
        return taken;
    }
}
