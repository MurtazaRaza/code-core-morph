package org.example.tasks;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class EffortTest {
    @ParameterizedTest
    @CsvSource({"10, quick", "29, quick", "30, short", "89, short", "90, long", "239, long", "240, huge"})
    void describesEstimates(int minutes, String expected) {
        assertEquals(expected, Effort.describe(minutes));
    }

    @Test
    void formatsEstimates() {
        assertEquals("45 min", Effort.format(45));
        assertEquals("2 h", Effort.format(120));
        assertEquals("1 h 30 min", Effort.format(90));
    }

    @Test
    void equalTasksAreEqual() {
        assertEquals(new Task("a", 1, 5), new Task("a", 1, 5));
        assertEquals(new Task("a", 1, 5).hashCode(), new Task("a", 1, 5).hashCode());
        assertEquals("a (P1, 5 min)", new Task("a", 1, 5).toString());
    }
}
