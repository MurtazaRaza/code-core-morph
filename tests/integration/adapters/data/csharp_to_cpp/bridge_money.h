/* The bridge of Shop.Money, a value type: it crosses as its amount and its currency (null for
 * default(Money)). */
#ifndef SHOP_BRIDGE_MONEY_H
#define SHOP_BRIDGE_MONEY_H

#include "codecoremorph_bridge.h"

#ifdef __cplusplus
extern "C" {
#endif

CCM_EXPORT ccm_status shop_Money_new(ccm_decimal amount, const char *currency,
                                     ccm_decimal *result_amount, char **result_currency);
CCM_EXPORT ccm_status shop_Money_zero(const char *currency, ccm_decimal *result_amount,
                                      char **result_currency);
CCM_EXPORT ccm_status shop_Money_add(ccm_decimal amount, const char *currency,
                                     ccm_decimal other_amount, const char *other_currency,
                                     ccm_decimal *result_amount, char **result_currency);
CCM_EXPORT ccm_status shop_Money_subtract(ccm_decimal amount, const char *currency,
                                          ccm_decimal other_amount, const char *other_currency,
                                          ccm_decimal *result_amount, char **result_currency);
CCM_EXPORT ccm_status shop_Money_times(ccm_decimal amount, const char *currency, int32_t quantity,
                                       ccm_decimal *result_amount, char **result_currency);
CCM_EXPORT ccm_status shop_Money_percent(ccm_decimal amount, const char *currency, int32_t percent,
                                         ccm_decimal *result_amount, char **result_currency);
CCM_EXPORT ccm_status shop_Money_equals(ccm_decimal amount, const char *currency,
                                        ccm_decimal other_amount, const char *other_currency,
                                        bool *result);
CCM_EXPORT ccm_status shop_Money_hash(ccm_decimal amount, const char *currency, int32_t *result);
CCM_EXPORT ccm_status shop_Money_to_string(ccm_decimal amount, const char *currency,
                                           char **result);

#ifdef __cplusplus
}
#endif

#endif
