package r;

import static org.mockito.Mockito.mock;

import org.mockito.Mock;

public class MockedTest {
    // Mockito's mocks are subclasses made at run time: of a class literal, and of a field's type.
    @Mock
    Mixed.Box<String> box;

    void stubs() {
        Mixed<?> mixed = mock(Mixed.class);
        Runnable task = mock(Runnable.class); // not the library's: nothing to record
    }
}
