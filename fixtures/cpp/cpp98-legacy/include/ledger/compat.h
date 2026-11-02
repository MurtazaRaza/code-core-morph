#ifndef LEDGER_COMPAT_H
#define LEDGER_COMPAT_H

// Portability helpers for compilers without the newer language features.

// A null pointer constant that converts to any pointer type but never to an integer, so
// overloads taking int and T* resolve the way callers expect. Some standard libraries already
// provide one as a macro.
#ifndef nullptr
const class nullptr_t_
{
public:
    template <class T>
    operator T*() const
    {
        return 0;
    }

    template <class C, class T>
    operator T C::*() const
    {
        return 0;
    }

private:
    void operator&() const;
} nullptr = {};
#endif

// Compile-time check: the array size is negative, and compilation fails, when cond is false.
#define LEDGER_STATIC_ASSERT(cond, name) typedef char ledger_static_assert_##name[(cond) ? 1 : -1]

// Declares the copy operations private and leaves them undefined.
#define LEDGER_NONCOPYABLE(Type) \
    Type(const Type&);           \
    Type& operator=(const Type&)

#endif
