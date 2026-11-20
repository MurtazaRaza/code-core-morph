// CcmInterop.java: the support of a Java façade's bridge. Written by CodeCoreMorph; do not
// edit. Java 8.
//
// The façades call the native library through CcmNative (generated from the bridge headers):
// every export returns a status, which check() turns into the exception the native code
// reported, or the one a call back into Java threw. A handle is the address of a native object;
// track() releases it when its façade becomes unreachable, and register()/wrap() keep an object
// that comes back from native code the same Java object.
package codecoremorph.interop;

import java.lang.ref.PhantomReference;
import java.lang.ref.ReferenceQueue;
import java.lang.ref.WeakReference;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

public final class CcmInterop {
    public static final int OK = 0;
    public static final int ERROR = 1;
    public static final int MANAGED = 2;

    private CcmInterop() {
    }

    static {
        CcmNative.load();
    }

    // ------------------------------------------------------------------ strings

    /** A string's UTF-8 bytes for a const char * parameter; null for null. */
    public static byte[] utf8(String text) {
        return text == null ? null : text.getBytes(StandardCharsets.UTF_8);
    }

    /** Strings' UTF-8 bytes for a const char ** array parameter; null for null. */
    public static byte[][] utf8s(String[] texts) {
        if (texts == null) {
            return null;
        }
        byte[][] out = new byte[texts.length][];
        for (int i = 0; i < texts.length; i++) {
            out[i] = utf8(texts[i]);
        }
        return out;
    }

    /** The string a char ** result held; null for null. */
    public static String string(byte[] utf8) {
        return utf8 == null ? null : new String(utf8, StandardCharsets.UTF_8);
    }

    // ------------------------------------------------------------------ statuses

    private static final ThreadLocal<Throwable> PENDING = new ThreadLocal<Throwable>();

    /** Throws what a non-OK status stands for. */
    public static void check(int status) {
        if (status == OK) {
            return;
        }
        if (status == MANAGED) {
            Throwable pending = PENDING.get();
            PENDING.remove();
            if (pending == null) {
                pending = new IllegalStateException("a call back into Java failed without an exception");
            }
            throw CcmInterop.<RuntimeException>sneaky(pending);
        }
        CcmError error = new CcmError(part(0), part(1), part(2), part(3));
        throw CcmInterop.<RuntimeException>sneaky(create(error));
    }

    /**
     * What a call back into Java returns when the Java threw: the exception is kept for the
     * façade, which rethrows the same object once the native code has returned.
     */
    public static int managed(Throwable thrown) {
        PENDING.set(thrown);
        return MANAGED;
    }

    private static String part(int part) {
        String text = string(errorPart(part));
        return text == null ? "" : text;
    }

    private static native byte[] errorPart(int part);

    // ------------------------------------------------------------------ exceptions

    /** The error a native export reported: the Java type it stands for, its message, fields. */
    public static final class CcmError {
        public final String type;
        public final String message;
        public final String param;
        public final Map<String, String> fields;

        CcmError(String type, String message, String param, String fields) {
            this.type = type;
            this.message = message.isEmpty() ? null : message;
            this.param = param;
            Map<String, String> parsed = new HashMap<String, String>();
            for (String line : fields.split("\n")) {
                int eq = line.indexOf('=');
                if (eq > 0) {
                    parsed.put(line.substring(0, eq), unescape(line.substring(eq + 1)));
                }
            }
            this.fields = Collections.unmodifiableMap(parsed);
        }

        public String field(String name) {
            return fields.get(name);
        }

        private static String unescape(String text) {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                if (c == '\\' && i + 1 < text.length()) {
                    char n = text.charAt(++i);
                    out.append(n == 'n' ? '\n' : n);
                } else {
                    out.append(c);
                }
            }
            return out.toString();
        }
    }

    private static Throwable create(CcmError error) {
        // A project's own exceptions first: CcmExceptions, beside this file, makes them.
        Throwable made = CcmExceptions.create(error);
        if (made != null) {
            return made;
        }
        String m = error.message;
        switch (error.type) {
            case "java.lang.IllegalArgumentException": return new IllegalArgumentException(m);
            case "java.lang.IllegalStateException": return new IllegalStateException(m);
            case "java.lang.NullPointerException": return new NullPointerException(m);
            case "java.lang.ArithmeticException": return new ArithmeticException(m);
            case "java.lang.NumberFormatException": return new NumberFormatException(m);
            case "java.lang.IndexOutOfBoundsException": return new IndexOutOfBoundsException(m);
            case "java.lang.ArrayIndexOutOfBoundsException": return new ArrayIndexOutOfBoundsException(m);
            case "java.lang.StringIndexOutOfBoundsException": return new StringIndexOutOfBoundsException(m);
            case "java.lang.UnsupportedOperationException": return new UnsupportedOperationException(m);
            case "java.lang.ClassCastException": return new ClassCastException(m);
            case "java.lang.NegativeArraySizeException": return new NegativeArraySizeException(m);
            case "java.lang.CloneNotSupportedException": return new CloneNotSupportedException(m);
            case "java.lang.InterruptedException": return new InterruptedException(m);
            case "java.lang.OutOfMemoryError": return new OutOfMemoryError(m);
            case "java.lang.Exception": return new Exception(m);
            case "java.lang.RuntimeException": return new RuntimeException(m);
            case "java.util.NoSuchElementException": return new java.util.NoSuchElementException(m);
            case "java.util.ConcurrentModificationException": return new java.util.ConcurrentModificationException(m);
            case "java.util.EmptyStackException": return new java.util.EmptyStackException();
            case "java.io.IOException": return new java.io.IOException(m);
            case "java.io.UncheckedIOException": return new java.io.UncheckedIOException(new java.io.IOException(m));
            default:
                return new IllegalStateException("the native code threw " + error.type
                    + (m == null ? "" : ": " + m) + ", which no factory makes (CcmExceptions)");
        }
    }

    @SuppressWarnings("unchecked")
    private static <T extends Throwable> T sneaky(Throwable thrown) throws T {
        throw (T) thrown;
    }

    // ------------------------------------------------------------------ handles

    /** Releases a handle: the generated X_release declarations are these. */
    public interface Release {
        void release(long handle);
    }

    private static final ReferenceQueue<Object> QUEUE = new ReferenceQueue<Object>();
    private static final Set<Tracked> LIVE = Collections.newSetFromMap(new ConcurrentHashMap<Tracked, Boolean>());

    private static final class Tracked extends PhantomReference<Object> {
        final long handle;
        final Release release;

        Tracked(Object owner, long handle, Release release) {
            super(owner, QUEUE);
            this.handle = handle;
            this.release = release;
        }
    }

    static {
        Thread cleaner = new Thread(new Runnable() {
            @Override
            public void run() {
                while (true) {
                    try {
                        Tracked tracked = (Tracked) QUEUE.remove();
                        LIVE.remove(tracked);
                        tracked.release.release(tracked.handle);
                    } catch (InterruptedException e) {
                        return;
                    } catch (Throwable ignored) {
                        // a release never throws; nothing else to do with one that did
                    }
                }
            }
        }, "codecoremorph-cleaner");
        cleaner.setDaemon(true);
        cleaner.start();
    }

    /** Releases the handle once its owner, the façade holding it, becomes unreachable. */
    public static long track(Object owner, long handle, Release release) {
        if (handle != 0) {
            LIVE.add(new Tracked(owner, handle, release));
        }
        return handle;
    }

    // ------------------------------------------------------------------ identity

    /** Makes the façade of a handle that came back from native code. */
    public interface Factory<T> {
        T create(long handle);
    }

    private static final Map<Long, WeakReference<Object>> OBJECTS = new HashMap<Long, WeakReference<Object>>();

    /** Records a new façade under its native object's key. */
    public static synchronized void register(long key, Object facade) {
        if (key != 0) {
            OBJECTS.put(key, new WeakReference<Object>(facade));
        }
    }

    /**
     * The façade of a native object: the one that exists, the new handle released, or a new one
     * from the factory. Null for a null handle.
     */
    public static synchronized <T> T wrap(long key, long handle, Release release, Factory<T> factory) {
        if (handle == 0) {
            return null;
        }
        WeakReference<Object> ref = OBJECTS.get(key);
        Object existing = ref == null ? null : ref.get();
        if (existing != null) {
            release.release(handle);
            @SuppressWarnings("unchecked")
            T same = (T) existing;
            return same;
        }
        if (OBJECTS.size() > 1024) {
            OBJECTS.values().removeIf(r -> r.get() == null);
        }
        T made = factory.create(handle);
        OBJECTS.put(key, new WeakReference<Object>(made));
        return made;
    }
}
