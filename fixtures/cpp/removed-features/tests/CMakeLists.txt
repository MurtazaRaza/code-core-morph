find_package(GTest REQUIRED)

add_executable(toolkit_tests
    logger_test.cpp
    parser_test.cpp
    shuffle_test.cpp
    functional_test.cpp
    transaction_test.cpp
    misc_test.cpp
)
target_link_libraries(toolkit_tests PRIVATE toolkit GTest::gtest_main)
add_test(NAME toolkit_tests COMMAND toolkit_tests)
