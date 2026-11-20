package r;

public class MixedTest {
    // An anonymous subclass that overrides a library class's method.
    Mixed.Box<String> box = new Mixed.Box<String>() {
        @Override
        public String get() {
            return "fixed";
        }
    };
}
