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
 * name, 3 the fields ("name=value" lines). Valid until the thread's next export call. */
CCM_EXPORT const char *ccm_error_part(int32_t part);
CCM_EXPORT void ccm_free(void *memory);
CCM_EXPORT char *ccm_strdup(const char *text);

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
