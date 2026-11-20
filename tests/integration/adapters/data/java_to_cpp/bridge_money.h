/* The bridge of shop.Money: an immutable value, crossing as its fields (cents, currency). */
#ifndef SHOP_BRIDGE_MONEY_H
#define SHOP_BRIDGE_MONEY_H

#include "codecoremorph_bridge.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Checks the fields as the constructor does. */
CCM_EXPORT ccm_status shop_Money_new(int64_t cents, const char *currency);
CCM_EXPORT ccm_status shop_Money_parse(const char *text, int64_t *result_cents,
                                       char **result_currency);
CCM_EXPORT ccm_status shop_Money_plus(int64_t cents, const char *currency, int64_t other_cents,
                                      const char *other_currency, int64_t *result_cents,
                                      char **result_currency);
CCM_EXPORT ccm_status shop_Money_times(int64_t cents, const char *currency, int32_t n,
                                       int64_t *result_cents);
CCM_EXPORT ccm_status shop_Money_percent(int64_t cents, const char *currency, int32_t percent,
                                         int64_t *result_cents);
CCM_EXPORT ccm_status shop_Money_compareTo(int64_t cents, const char *currency,
                                           int64_t other_cents, const char *other_currency,
                                           int32_t *result);
CCM_EXPORT ccm_status shop_Money_hashCode(int64_t cents, const char *currency, int32_t *result);
CCM_EXPORT ccm_status shop_Money_toString(int64_t cents, const char *currency, char **result);

#ifdef __cplusplus
}
#endif

#endif
