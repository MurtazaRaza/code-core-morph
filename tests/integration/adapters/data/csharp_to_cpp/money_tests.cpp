// MoneyTests, translated from C# by hand for the test: a GoogleTest test per C# test case, the
// parameterized one a TEST_P.
#include <gtest/gtest.h>

#include <string>

#include "codecoremorph_exceptions.hpp"
#include "money.hpp"

namespace {

shop::Money eur(const char* amount) { return shop::Money(ccm::Decimal::parse(amount), "EUR"); }

}  // namespace

TEST(MoneyTests, AddingKeepsTheScale) {
    ASSERT_EQ((eur("1.10") + eur("1.0")).to_string(), "2.10 EUR");
}

TEST(MoneyTests, TimesMultipliesTheAmount) {
    ASSERT_EQ(eur("2.50").times(3).to_string(), "7.50 EUR");
}

TEST(MoneyTests, CurrenciesDoNotMix) {
    try {
        (void)eur("1").add(shop::Money(ccm::Decimal(1), "USD"));
        FAIL() << "no exception";
    } catch (const ccm::InvalidOperationException& ex) {
        ASSERT_NE(std::string(ex.what()).find("USD"), std::string::npos);
    }
}

struct Percent {
    int percent;
    const char* expected;
};

class MoneyTests_PercentIsUnrounded : public ::testing::TestWithParam<Percent> {};

TEST_P(MoneyTests_PercentIsUnrounded, Run) {
    ASSERT_EQ(eur("3.33").percent(GetParam().percent).to_string(), GetParam().expected);
}

INSTANTIATE_TEST_SUITE_P(Cases, MoneyTests_PercentIsUnrounded,
                         ::testing::Values(Percent{10, "0.333 EUR"}, Percent{15, "0.4995 EUR"},
                                           Percent{33, "1.0989 EUR"}));
