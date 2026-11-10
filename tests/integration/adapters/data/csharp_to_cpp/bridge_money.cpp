#include "money.h"

#include "codecoremorph_bridge.hpp"
#include "money.hpp"

namespace bridge = ccm::bridge;

namespace {

// A Money from the façade's fields: a null currency is default(Money).
shop::Money money(ccm_decimal amount, const char* currency) {
    return currency == nullptr ? shop::Money() : shop::Money(bridge::dec(amount), currency);
}

void out_money(const shop::Money& m, ccm_decimal* amount, char** currency) {
    *amount = bridge::dec(m.amount());
    bridge::out_str(currency, m.currency());
}

}  // namespace

extern "C" {

CCM_EXPORT ccm_status shop_Money_new(ccm_decimal amount, const char* currency,
                                     ccm_decimal* result_amount, char** result_currency) {
    return bridge::guard([&] {
        out_money(shop::Money(bridge::dec(amount), bridge::str(currency, "currency")),
                  result_amount, result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_zero(const char* currency, ccm_decimal* result_amount,
                                      char** result_currency) {
    return bridge::guard([&] {
        out_money(shop::Money::zero(bridge::str(currency, "currency")), result_amount,
                  result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_add(ccm_decimal amount, const char* currency,
                                     ccm_decimal other_amount, const char* other_currency,
                                     ccm_decimal* result_amount, char** result_currency) {
    return bridge::guard([&] {
        out_money(money(amount, currency).add(money(other_amount, other_currency)), result_amount,
                  result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_subtract(ccm_decimal amount, const char* currency,
                                          ccm_decimal other_amount, const char* other_currency,
                                          ccm_decimal* result_amount, char** result_currency) {
    return bridge::guard([&] {
        out_money(money(amount, currency).subtract(money(other_amount, other_currency)),
                  result_amount, result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_times(ccm_decimal amount, const char* currency, int32_t quantity,
                                       ccm_decimal* result_amount, char** result_currency) {
    return bridge::guard([&] {
        out_money(money(amount, currency).times(quantity), result_amount, result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_percent(ccm_decimal amount, const char* currency, int32_t percent,
                                         ccm_decimal* result_amount, char** result_currency) {
    return bridge::guard([&] {
        out_money(money(amount, currency).percent(percent), result_amount, result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_equals(ccm_decimal amount, const char* currency,
                                        ccm_decimal other_amount, const char* other_currency,
                                        bool* result) {
    return bridge::guard(
        [&] { *result = money(amount, currency).equals(money(other_amount, other_currency)); });
}

CCM_EXPORT ccm_status shop_Money_hash(ccm_decimal amount, const char* currency, int32_t* result) {
    return bridge::guard(
        [&] { *result = static_cast<int32_t>(money(amount, currency).hash() & 0x7fffffff); });
}

CCM_EXPORT ccm_status shop_Money_to_string(ccm_decimal amount, const char* currency,
                                           char** result) {
    return bridge::guard([&] { bridge::out_str(result, money(amount, currency).to_string()); });
}

}  // extern "C"
