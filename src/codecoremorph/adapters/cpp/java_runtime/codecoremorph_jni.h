/* codecoremorph_jni.h: what the generated JNI glue of a Java façade's bridge uses. Written by
 * CodeCoreMorph; do not edit.
 *
 * The glue (codecoremorph_jni.g.c, generated from the bridge headers) turns each Java native
 * method of codecoremorph.interop.CcmNative into a call of its export: strings cross as UTF-8
 * byte arrays (Java encodes and decodes them, so they are real UTF-8, not JNI's modified
 * UTF-8), out parameters as arrays of one element, contexts as JNI global references, and each
 * function pointer is a trampoline that calls the static CcmNative.<typedef>_call method. */
#ifndef CODECOREMORPH_JNI_H
#define CODECOREMORPH_JNI_H

#include <jni.h>

#include "codecoremorph_bridge.h"

#ifdef __cplusplus
extern "C" {
#endif

/* codecoremorph/interop/CcmNative, held from JNI_OnLoad. */
extern jclass ccm_jni_native;

/* The calling thread's JNIEnv: a thread native code started is attached as a daemon. */
JNIEnv *ccm_jni_env(void);

/* A malloc'd NUL-terminated copy of a UTF-8 byte array; NULL for null. */
char *ccm_jni_utf8(JNIEnv *env, jbyteArray bytes);
/* A UTF-8 C string as a byte array; NULL for NULL. */
jbyteArray ccm_jni_bytes(JNIEnv *env, const char *text);
/* out[0] = the string's bytes (or null), and the string freed with ccm_free. */
void ccm_jni_put_string(JNIEnv *env, jobjectArray out, char *text);
/* A new byte[1][]: where a call into Java puts a string it returns. */
jobjectArray ccm_jni_string_slot(JNIEnv *env);
/* A malloc'd copy of out[0] (a call into Java set it); NULL for null. */
char *ccm_jni_take_string(JNIEnv *env, jobjectArray out);

/* An array of strings (byte arrays) as malloc'd C strings, its count in *count (-1 for null);
 * ccm_jni_free_strings frees them. */
char **ccm_jni_strings(JNIEnv *env, jobjectArray array, int32_t *count);
void ccm_jni_free_strings(char **strings, int32_t count);

/* A Java object as a context native code keeps: a global reference. */
void *ccm_jni_ref(JNIEnv *env, jobject object);
/* The ccm_release_fn of such a context. */
void ccm_jni_release(void *context);

/* The static CcmNative method a trampoline calls, looked up once. */
jmethodID ccm_jni_method(JNIEnv *env, jmethodID *cache, const char *name, const char *signature);

/* What a trampoline returns when the call left a Java exception pending (only an Error the
 * generated method didn't catch): CcmInterop.managed keeps it, CCM_MANAGED. */
ccm_status ccm_jni_pending(JNIEnv *env);

#ifdef __cplusplus
}
#endif

#endif
