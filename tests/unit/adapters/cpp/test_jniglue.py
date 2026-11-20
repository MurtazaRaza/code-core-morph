"""The C JNI glue of a Java façade's bridge for a C++ target (SPEC §11.12, D-92)."""

from __future__ import annotations

from tests.unit.adapters.java.test_jni import cart_bridge

from codecoremorph.adapters.cpp.jniglue import c_glue
from codecoremorph.adapters.java.jni import read_bridge


def glue() -> str:
    return c_glue(read_bridge(cart_bridge()))


def test_the_glue_includes_the_bridge_headers_by_name() -> None:
    text = glue()
    assert '#include "codecoremorph_jni.h"' in text
    assert '#include "cart.h"' in text


def test_each_native_method_converts_calls_and_copies_out_on_success() -> None:
    text = glue()
    assert (
        "JNIEXPORT jint JNICALL Java_codecoremorph_interop_CcmNative_shop_1Cart_1new_1raw("
        "JNIEnv *env, jclass cls, jbyteArray currency, jlongArray result) {"
    ) in text
    assert "char *c_currency = ccm_jni_utf8(env, currency);" in text
    assert "shop_Cart *c_result = NULL;" in text
    assert "jint status = (jint)shop_Cart_new(c_currency, &c_result);" in text
    new = text.split("shop_1Cart_1new_1raw", 1)[1].split("\n}\n", 1)[0]
    # The out parameter is copied back only for CCM_OK; the string in is freed whatever.
    assert new.index("if (status == CCM_OK)") < new.index("SetLongArrayRegion")
    assert "free(c_currency);" in new
    total = text.split("shop_1Cart_1total_1raw", 1)[1].split("\n}\n", 1)[0]
    assert "(const shop_Cart *)(intptr_t)this_" in total
    assert "ccm_jni_put_string(env, result_currency, c_result_currency);" in total
    assert "ccm_free(c_result_currency);" in total


def test_contexts_are_global_references_released_by_their_owner() -> None:
    text = glue()
    managed = text.split("shop_1PriceRule_1from_1managed", 1)[1].split("\n}\n", 1)[0]
    assert "void *c_context = ccm_jni_ref(env, context);" in managed
    assert (
        "shop_PriceRule_from_managed(c_context, ccm_jni_release, "
        "ccm_jni_cb_shop_PriceRule_apply_fn, &c_result)"
    ) in managed
    assert "ccm_jni_release(c_context);" not in managed  # native code keeps it
    count = text.split("shop_1Cart_1count", 1)[1].split("\n}\n", 1)[0]
    assert "ccm_jni_release(c_context);" in count  # a context for the call only


def test_arrays_cross_with_their_counts() -> None:
    text = glue()
    checkout = text.split("shop_1Cart_1checkout_1raw", 1)[1].split("\n}\n", 1)[0]
    assert "char **c_keys = ccm_jni_strings(env, keys, &n_keys);" in checkout
    assert "int32_t n_values = values == NULL ? -1 :" in checkout
    assert "(const int32_t *)c_values, n_values" in checkout
    assert "jbyteArray a_result = (*env)->NewByteArray(env, n_result);" in checkout
    assert "ccm_free(c_result);" in checkout
    assert "c_found ? JNI_TRUE : JNI_FALSE" in checkout
    assert "flag == JNI_TRUE" in checkout
    assert "void *c_director = NULL;" in checkout


def test_a_trampoline_calls_the_static_method_with_the_context() -> None:
    text = glue()
    assert (
        "static ccm_status ccm_jni_cb_shop_PriceRule_apply_fn(void *context, const char *item, "
        "int32_t category, int64_t price_cents, int64_t *result_cents, char **result_currency) {"
    ) in text
    assert ('"shop_PriceRule_apply_fn_call", "(Ljava/lang/Object;[BIJ[J[[B)I");') in text
    assert "CallStaticIntMethod(env, ccm_jni_native, mid, (jobject)context, j_item," in text
    assert "*result_currency = ccm_jni_take_string(env, j_result_currency);" in text
    assert "if (j_item != NULL) (*env)->DeleteLocalRef(env, j_item);" in text
    assert "*result = v == JNI_TRUE;" in text
