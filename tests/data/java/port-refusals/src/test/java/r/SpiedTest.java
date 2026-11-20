package r;

import org.mockito.Mockito;

public class SpiedTest {
    // A spy is a mock too, of the spied object's class.
    Mixed.Box<String> box = Mockito.spy(new Mixed.Box<String>());
}
