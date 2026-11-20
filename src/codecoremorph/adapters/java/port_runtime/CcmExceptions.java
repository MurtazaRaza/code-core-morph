// CcmExceptions.java: the project's own exceptions, made from what the native code reported.
// The scaffold writes it once; each port unit whose native code throws one of the project's
// exception types adds its case. Java 8.
package codecoremorph.interop;

final class CcmExceptions {
    private CcmExceptions() {
    }

    /** The project's exception the error names, or null for another type. */
    static Throwable create(CcmInterop.CcmError error) {
        switch (error.type) {
            default:
                return null;
        }
    }
}
