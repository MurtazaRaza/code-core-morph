find_package(GTest REQUIRED)

add_executable(ledger_tests
    money_test.cpp
    format_test.cpp
    parse_test.cpp
    inventory_test.cpp
    transaction_test.cpp
    text_test.cpp
)
target_link_libraries(ledger_tests PRIVATE ledger GTest::gtest_main)
add_test(NAME ledger_tests COMMAND ledger_tests)
