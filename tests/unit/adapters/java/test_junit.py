"""JUnit sources: test methods, assertions, disabled tests, removing tests, and the
determinism lint (SPEC §7.11, §11.2; I-3, I-4)."""

from __future__ import annotations

from codecoremorph.adapters.java.junit import (
    assertions,
    disabled_markers,
    drop_cases,
    find_cases,
    mask_java,
    nondeterminism,
)

JUNIT4 = """\
package org.example.ledger;

import static org.junit.Assert.assertEquals;

import org.junit.Ignore;
import org.junit.Test;

public class MoneyTest {
    private static final String TEXT = \"\"\"
        @Test void notATest() {}
        \"\"\";

    @Test
    public void parses() {
        // assertEquals(1, 2) in a comment is not an assertion
        assertEquals(1250, Money.parse("12.50 EUR").getCents());
        assertEquals("@Test", "@Test");
    }

    @Test(expected = IllegalArgumentException.class)
    public void refusesABadCurrency() {
        new Money(1, "EURO");
    }

    @Ignore("flaky")
    @Test
    public void ignored() {
    }

    private void helper() {
        assertEquals(1, 1);
    }
}
"""

JUNIT5 = """\
package org.example.tasks;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;

class TaskQueueTest {
    @org.junit.jupiter.api.Test
    void ordersByPriority() {
        assertThat(queue.next())
            .isEqualTo(task)
            .isNotNull();
        IllegalArgumentException e =
            assertThrows(IllegalArgumentException.class, () -> new Task("x", 0, 5));
    }

    @ParameterizedTest
    @CsvSource({"10, quick", "29, quick"})
    void describes(int minutes, String expected) {
        Assertions.assertEquals(expected, Effort.describe(minutes));
    }

    @Nested
    class WhenEmpty {
        @Test
        void hasNoNext() {
            assertNull(queue.next());
        }
    }

    @Disabled
    @Test
    void later() {
        Assumptions.assumeTrue(fast);
    }
}
"""

JUNIT3 = """\
package legacy;

public class OldTest extends junit.framework.TestCase {
    public void testAdds() {
        assertEquals(2, 1 + 1);
    }

    public void helperNotATest() {
    }
}
"""


def test_the_mask_blanks_comments_strings_text_blocks_and_chars() -> None:
    text = 'a // x\nb /* y */ "s\\"t" \'q\' """\nblock "inner"\n""" c'
    masked = mask_java(text)
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    assert "x" not in masked
    assert "y" not in masked
    assert "inner" not in masked
    assert masked.endswith(" c")
    assert "q" not in masked
    kept = mask_java(text, strings=False)
    assert '"s\\"t"' in kept
    assert "x" not in kept


def test_junit4_cases_with_their_ids_and_assertions() -> None:
    cases = find_cases(JUNIT4)
    assert [c.test_id for c in cases] == [
        "junit:org.example.ledger.MoneyTest#parses",
        "junit:org.example.ledger.MoneyTest#refusesABadCurrency",
        "junit:org.example.ledger.MoneyTest#ignored",
    ]
    assert assertions(JUNIT4, cases[0]) == [
        'assertEquals(1250,Money.parse("12.50 EUR").getCents())',
        'assertEquals("@Test","@Test")',
    ]
    assert assertions(JUNIT4, cases[1]) == ["@Test(expected = IllegalArgumentException.class)"]
    assert cases[0].line == 13


def test_junit5_cases_nested_classes_and_chained_assertions() -> None:
    cases = find_cases(JUNIT5)
    assert [c.test_id for c in cases] == [
        "junit:org.example.tasks.TaskQueueTest#ordersByPriority",
        "junit:org.example.tasks.TaskQueueTest#describes",
        "junit:org.example.tasks.TaskQueueTest$WhenEmpty#hasNoNext",
        "junit:org.example.tasks.TaskQueueTest#later",
    ]
    assert assertions(JUNIT5, cases[0]) == [
        "assertThat(queue.next()).isEqualTo(task).isNotNull()",
        'assertThrows(IllegalArgumentException.class,()-> new Task("x",0,5))',
    ]
    assert assertions(JUNIT5, cases[1]) == [
        "Assertions.assertEquals(expected,Effort.describe(minutes))"
    ]


def test_junit3_methods_in_a_test_case_class() -> None:
    cases = find_cases(JUNIT3)
    assert [c.test_id for c in cases] == ["junit:legacy.OldTest#testAdds"]
    assert assertions(JUNIT3, cases[0]) == ["assertEquals(2,1 + 1)"]


def test_disabled_tests_and_assumptions_are_markers() -> None:
    assert disabled_markers("A.java", JUNIT4) == ["A.java:25: @Ignore"]
    assert disabled_markers("B.java", JUNIT5) == [
        "B.java:30: @Disabled",
        "B.java:33: Assumptions.assumeTrue",
    ]


def test_dropping_cases_keeps_the_rest() -> None:
    text = drop_cases(JUNIT5, {"junit:org.example.tasks.TaskQueueTest#describes"})
    assert "describes" not in text
    assert "@CsvSource" not in text
    assert [c.method for c in find_cases(text)] == ["ordersByPriority", "hasNoNext", "later"]
    assert drop_cases(JUNIT5, set()) == JUNIT5


def test_the_determinism_lint() -> None:
    text = """\
class T {
    @Test void t() throws Exception {
        long now = System.currentTimeMillis();
        Random seeded = new Random(42);
        Random any = new Random();
        java.nio.file.Files.readAllLines(path);
        Thread.sleep(10);
        String s = "System.nanoTime()";
    }
}
"""
    assert nondeterminism("T.java", text) == [
        "T.java:3: reads the clock (System.currentTimeMillis()",
        "T.java:5: uses randomness (new Random())",
        "T.java:6: uses the filesystem (Files.readAllLines()",
        "T.java:7: depends on thread timing (Thread.sleep()",
    ]
