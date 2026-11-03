find_package(GTest REQUIRED)

add_executable(billing_tests billing_test.cpp)
target_link_libraries(billing_tests PRIVATE billing GTest::gtest_main)
add_test(NAME billing_tests COMMAND billing_tests)
