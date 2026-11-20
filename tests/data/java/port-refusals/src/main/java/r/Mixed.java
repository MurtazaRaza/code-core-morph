package r;

import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;

public class Mixed<T extends Number> {
    public static int counter;
    public static final List<String> NAMES = new java.util.ArrayList<String>();
    public static final int LIMIT = 3;
    private static final java.util.regex.Pattern SPACES = java.util.regex.Pattern.compile(" +");
    protected int seen;
    private final ConcurrentHashMap<String, T> cache = new ConcurrentHashMap<String, T>();
    private ExecutorService pool;

    public BigDecimal total() { return BigDecimal.ZERO; }

    public Object reflect(Object o) throws Exception {
        Method m = o.getClass().getMethod("toString");
        return m.invoke(o);
    }

    public Box<String> box() { return new Box<String>(); }

    public native int fast(int x);

    public void spawn() { new Thread(() -> { }).start(); }

    public static class Box<V> {
        public V value;

        public V get() { return value; }
    }
}
