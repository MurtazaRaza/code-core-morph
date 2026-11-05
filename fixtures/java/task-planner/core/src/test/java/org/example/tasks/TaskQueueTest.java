package org.example.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Arrays;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

class TaskQueueTest {
    private TaskQueue queue;

    @BeforeEach
    void setUp() {
        queue = new TaskQueue();
    }

    @Test
    void ordersByPriorityThenEstimateThenArrival() {
        queue.add(new Task("write", 2, 60));
        queue.add(new Task("call", 1, 15));
        queue.add(new Task("review", 2, 30));
        queue.add(new Task("lunch", 2, 30));
        assertEquals("call", queue.next().getTitle());
        assertEquals("review", queue.next().getTitle());
        assertEquals("lunch", queue.next().getTitle());
        assertEquals("write", queue.next().getTitle());
    }

    @Test
    void fillsTheTimeGivenAndKeepsTheRest() {
        queue.add(new Task("a", 1, 120));
        queue.add(new Task("b", 2, 300));
        queue.add(new Task("c", 3, 60));
        assertEquals(Arrays.asList(new Task("a", 1, 120), new Task("c", 3, 60)), queue.fill(240));
        assertEquals(1, queue.size());
        assertEquals("b", queue.next().getTitle());
    }

    @Test
    void refusesABadPriority() {
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class, () -> new Task("x", 0, 5));
        assertEquals("priority must be 1 to 5, not 0", e.getMessage());
    }

    @Nested
    class WhenEmpty {
        @Test
        void hasNoNext() {
            assertNull(queue.next());
            assertEquals(0, queue.size());
        }

        @Test
        void fillsNothing() {
            assertEquals(0, queue.fill(60).size());
        }
    }
}
