/* codecoremorph_bridge.h: the C types of a C# façade's bridge. Written by CodeCoreMorph; do not
 * edit.
 *
 * Every export returns a ccm_status. On CCM_ERROR the façade reads the error with
 * ccm_error_part and throws the .NET exception it names; on CCM_MANAGED it rethrows the exception
 * a call into C# threw. A bridge takes only these types: fixed-width integers, bool, float,
 * double, ccm_decimal, const char * (a UTF-8 string in), char ** (a string out, freed by the
 * façade with ccm_free), handles (pointers to opaque structs declared by CCM_HANDLE), void *
 * contexts, pointers to the above as out parameters, and function pointer typedefs. */
#ifndef CODECOREMORPH_BRIDGE_H
#define CODECOREMORPH_BRIDGE_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#define CCM_EXPORT __declspec(dllexport)
#else
#define CCM_EXPORT __attribute__((visibility("default")))
#endif

typedef int32_t ccm_status;
#define CCM_OK 0
#define CCM_ERROR 1
#define CCM_MANAGED 2

/* A .NET decimal, as decimal.GetBits gives it. */
typedef struct ccm_decimal {
    int32_t lo;
    int32_t mid;
    int32_t hi;
    int32_t flags;
} ccm_decimal;

/* Frees what a context pointer holds (a GCHandle), when the native side lets go of it. */
typedef void (*ccm_release_fn)(void *context);

/* An opaque handle to a shared C++ object, with its release and identity functions. */
#define CCM_HANDLE(name)                                                                       \
    typedef struct name name;                                                                  \
    CCM_EXPORT void name##_release(name *handle);                                              \
    CCM_EXPORT uint64_t name##_key(const name *handle)

/* The part of the calling thread's last error: 0 the .NET type, 1 the message, 2 the parameter
 * name, 3 the fields ("name=value" lines), 4 and 5 an ArgumentOutOfRangeException's actual
 * value, its .NET type (empty for none) and its invariant data (D-97). Valid until the thread's
 * next export call. */
CCM_EXPORT const char *ccm_error_part(int32_t part);
/* Whether the calling thread's last error is an OperationCanceledException with a token that
 * can be canceled; origin gets the C# token's GCHandle, or null for a token native code made
 * (D-97). Valid as ccm_error_part's parts are. */
CCM_EXPORT bool ccm_error_token(void **origin);
CCM_EXPORT void ccm_free(void *memory);
CCM_EXPORT char *ccm_strdup(const char *text);

/* A C# exception, recorded by the façades' support on the calling thread just before a call
 * into C# returns CCM_MANAGED (or a C# task fails): its .NET types, most derived first, one per
 * line; its message; and a GCHandle of it, which the native side releases with release. The
 * native side throws the mirror of the first of its types the bridge knows, carrying the
 * handle, so ported code catches it by type and a façade throws the C# object again (D-96). */
CCM_EXPORT void ccm_set_managed(const char *types, const char *message, void *exception,
                                ccm_release_fn release);
/* The C# exception the calling thread's last export failed with (CCM_MANAGED): its GCHandle,
 * kept until the thread's next export call, or null. */
CCM_EXPORT void *ccm_error_managed(void);

/* Whether native code holds a director's object besides its C# façade's handle. A director is
 * the native object of a C# class that derives from a ported one: its virtual members call the
 * C# object's (D-87). The façades' support asks after each garbage collection, and holds the C#
 * object weakly while only its façade holds the director. */
CCM_EXPORT bool ccm_director_shared(const void *director);

#ifdef __cplusplus
}

namespace ccm::bridge {
template <class Handle>
struct handle_traits;
}

/* C++ only, in the handle's header: ties a handle type to the class it holds. */
#define CCM_HANDLE_CLASS(Handle, Type)                                                         \
    namespace ccm::bridge {                                                                    \
    template <>                                                                                \
    struct handle_traits<Handle> {                                                             \
        using type = Type;                                                                     \
    };                                                                                         \
    }
#endif

#endif
