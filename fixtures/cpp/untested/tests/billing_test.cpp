#include <gtest/gtest.h>

#include "billing/tariff.h"

TEST(Billing, FormatsWholeAmounts) {
    EXPECT_EQ("1.00", billing::formatCents(100));
}
