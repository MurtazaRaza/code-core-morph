package org.example.app;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Date;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

class PlannerTest {
    private static final Date MONDAY = new Date(1717372800000L); // 2024-06-03 00:00 UTC

    @Test
    void plansTheDayFromNine() {
        Planner planner = new Planner();
        planner.init();
        planner.add("Standup", 1, 15);
        planner.add("Fix the build", 1, 90);
        planner.add("Write the report", 3, 240);
        planner.add("Read mail", 4, 30);
        planner.add("Plan next week", 2, 180);
        String expected = String.format("09:00 Standup                  15 min, quick%n"
                + "09:15 Fix the build            1 h 30 min, long%n"
                + "10:45 Plan next week           3 h, long%n"
                + "13:45 Read mail                30 min, short%n"
                + "Left for tomorrow: 1 task(s)%n");
        assertEquals(expected, planner.plan(MONDAY));
    }

    @Test
    void needsInit() {
        assertThrows(IllegalStateException.class, () -> new Planner().plan(MONDAY));
    }

    @ParameterizedTest
    @CsvSource({"1, urgent", "2, high", "3, normal", "4, low", "5, low"})
    void namesPriorities(int priority, String name) {
        assertEquals(name, Planner.priorityName(priority));
    }
}
