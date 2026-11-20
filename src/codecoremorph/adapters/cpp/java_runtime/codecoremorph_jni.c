/* codecoremorph_jni.c: the fixed part of a Java façade's JNI glue. Written by CodeCoreMorph;
 * do not edit. */
#include "codecoremorph_jni.h"

#include <stdlib.h>
#include <string.h>

static JavaVM *ccm_jni_vm;
jclass ccm_jni_native;
static jclass ccm_jni_interop;
static jclass ccm_jni_byte_array;
static jmethodID ccm_jni_managed;

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved) {
    (void)reserved;
    JNIEnv *env = NULL;
    if ((*vm)->GetEnv(vm, (void **)&env, JNI_VERSION_1_8) != JNI_OK) {
        return JNI_ERR;
    }
    ccm_jni_vm = vm;
    jclass native = (*env)->FindClass(env, "codecoremorph/interop/CcmNative");
    jclass interop = (*env)->FindClass(env, "codecoremorph/interop/CcmInterop");
    jclass bytes = (*env)->FindClass(env, "[B");
    if (native == NULL || interop == NULL || bytes == NULL) {
        return JNI_ERR;
    }
    ccm_jni_native = (jclass)(*env)->NewGlobalRef(env, native);
    ccm_jni_interop = (jclass)(*env)->NewGlobalRef(env, interop);
    ccm_jni_byte_array = (jclass)(*env)->NewGlobalRef(env, bytes);
    ccm_jni_managed =
        (*env)->GetStaticMethodID(env, interop, "managed", "(Ljava/lang/Throwable;)I");
    if (ccm_jni_managed == NULL) {
        return JNI_ERR;
    }
    return JNI_VERSION_1_8;
}

JNIEnv *ccm_jni_env(void) {
    JNIEnv *env = NULL;
    jint got = (*ccm_jni_vm)->GetEnv(ccm_jni_vm, (void **)&env, JNI_VERSION_1_8);
    if (got == JNI_EDETACHED) {
        if ((*ccm_jni_vm)->AttachCurrentThreadAsDaemon(ccm_jni_vm, (void **)&env, NULL) != JNI_OK) {
            return NULL;
        }
    } else if (got != JNI_OK) {
        return NULL;
    }
    return env;
}

char *ccm_jni_utf8(JNIEnv *env, jbyteArray bytes) {
    if (bytes == NULL) {
        return NULL;
    }
    jsize n = (*env)->GetArrayLength(env, bytes);
    char *copy = (char *)malloc((size_t)n + 1);
    if (copy == NULL) {
        return NULL;
    }
    (*env)->GetByteArrayRegion(env, bytes, 0, n, (jbyte *)copy);
    copy[n] = '\0';
    return copy;
}

jbyteArray ccm_jni_bytes(JNIEnv *env, const char *text) {
    if (text == NULL) {
        return NULL;
    }
    jsize n = (jsize)strlen(text);
    jbyteArray bytes = (*env)->NewByteArray(env, n);
    if (bytes != NULL) {
        (*env)->SetByteArrayRegion(env, bytes, 0, n, (const jbyte *)text);
    }
    return bytes;
}

void ccm_jni_put_string(JNIEnv *env, jobjectArray out, char *text) {
    jbyteArray bytes = ccm_jni_bytes(env, text);
    if (out != NULL && (*env)->GetArrayLength(env, out) > 0) {
        (*env)->SetObjectArrayElement(env, out, 0, bytes);
    }
    if (bytes != NULL) {
        (*env)->DeleteLocalRef(env, bytes);
    }
    ccm_free(text);
}

jobjectArray ccm_jni_string_slot(JNIEnv *env) {
    return (*env)->NewObjectArray(env, 1, ccm_jni_byte_array, NULL);
}

char *ccm_jni_take_string(JNIEnv *env, jobjectArray out) {
    jbyteArray bytes = (jbyteArray)(*env)->GetObjectArrayElement(env, out, 0);
    char *copy = ccm_jni_utf8(env, bytes);
    if (bytes != NULL) {
        (*env)->DeleteLocalRef(env, bytes);
    }
    return copy;
}

char **ccm_jni_strings(JNIEnv *env, jobjectArray array, int32_t *count) {
    if (array == NULL) {
        *count = -1;
        return NULL;
    }
    jsize n = (*env)->GetArrayLength(env, array);
    char **strings = (char **)calloc((size_t)n + 1, sizeof(char *));
    for (jsize i = 0; strings != NULL && i < n; i++) {
        jbyteArray bytes = (jbyteArray)(*env)->GetObjectArrayElement(env, array, i);
        strings[i] = ccm_jni_utf8(env, bytes);
        if (bytes != NULL) {
            (*env)->DeleteLocalRef(env, bytes);
        }
    }
    *count = (int32_t)n;
    return strings;
}

void ccm_jni_free_strings(char **strings, int32_t count) {
    if (strings == NULL) {
        return;
    }
    for (int32_t i = 0; i < count; i++) {
        free(strings[i]);
    }
    free((void *)strings);
}

void *ccm_jni_ref(JNIEnv *env, jobject object) {
    return object == NULL ? NULL : (void *)(*env)->NewGlobalRef(env, object);
}

void ccm_jni_release(void *context) {
    if (context == NULL) {
        return;
    }
    JNIEnv *env = ccm_jni_env();
    if (env != NULL) {
        (*env)->DeleteGlobalRef(env, (jobject)context);
    }
}

jmethodID ccm_jni_method(JNIEnv *env, jmethodID *cache, const char *name, const char *signature) {
    if (*cache == NULL) {
        *cache = (*env)->GetStaticMethodID(env, ccm_jni_native, name, signature);
    }
    return *cache;
}

ccm_status ccm_jni_pending(JNIEnv *env) {
    jthrowable thrown = (*env)->ExceptionOccurred(env);
    if (thrown == NULL) {
        return CCM_OK;
    }
    (*env)->ExceptionClear(env);
    jint status = (*env)->CallStaticIntMethod(env, ccm_jni_interop, ccm_jni_managed, thrown);
    (*env)->DeleteLocalRef(env, thrown);
    if ((*env)->ExceptionCheck(env)) {
        (*env)->ExceptionClear(env);
    }
    return status == CCM_OK ? CCM_MANAGED : status;
}

/* CcmInterop's own natives. */

JNIEXPORT jbyteArray JNICALL Java_codecoremorph_interop_CcmInterop_errorPart(JNIEnv *env, jclass cls,
                                                                            jint part) {
    (void)cls;
    return ccm_jni_bytes(env, ccm_error_part(part));
}
