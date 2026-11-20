#include "money.h"

#include "codecoremorph_bridge.hpp"
#include "money.hpp"

namespace bridge = ccm::bridge;

namespace {

shop::Money money(int64_t cents, const char* currency) {
    return {cents, bridge::str(currency, "currency")};
}

void out(const shop::Money& m, int64_t* result_cents, char** result_currency) {
    *result_cents = m.cents();
    bridge::out_str(result_currency, m.currency());
}

}  // namespace

extern "C" {

CCM_EXPORT ccm_status shop_Money_new(int64_t cents, const char* currency) {
    return bridge::guard([&] { (void)money(cents, currency); });
}

CCM_EXPORT ccm_status shop_Money_parse(const char* text, int64_t* result_cents,
                                       char** result_currency) {
    return bridge::guard(
        [&] { out(shop::Money::parse(bridge::str(text)), result_cents, result_currency); });
}

CCM_EXPORT ccm_status shop_Money_plus(int64_t cents, const char* currency, int64_t other_cents,
                                      const char* other_currency, int64_t* result_cents,
                                      char** result_currency) {
    return bridge::guard([&] {
        out(money(cents, currency).plus(money(other_cents, other_currency)), result_cents,
            result_currency);
    });
}

CCM_EXPORT ccm_status shop_Money_times(int64_t cents, const char* currency, int32_t n,
                                       int64_t* result_cents) {
    return bridge::guard([&] { *result_cents = money(cents, currency).times(n).cents(); });
}

CCM_EXPORT ccm_status shop_Money_percent(int64_t cents, const char* currency, int32_t percent,
                                         int64_t* result_cents) {
    return bridge::guard(
        [&] { *result_cents = money(cents, currency).percent(percent).cents(); });
}

CCM_EXPORT ccm_status shop_Money_compareTo(int64_t cents, const char* currency,
                                           int64_t other_cents, const char* other_currency,
                                           int32_t* result) {
    return bridge::guard([&] {
        *result = money(cents, currency).compare_to(money(other_cents, other_currency));
    });
}

CCM_EXPORT ccm_status shop_Money_hashCode(int64_t cents, const char* currency, int32_t* result) {
    return bridge::guard([&] { *result = money(cents, currency).hash_code(); });
}

CCM_EXPORT ccm_status shop_Money_toString(int64_t cents, const char* currency, char** result) {
    return bridge::guard([&] { bridge::out_str(result, money(cents, currency).to_string()); });
}

}  // extern "C"
