find_package(GTest REQUIRED)

add_executable(meta_tests traits_test.cpp math_test.cpp format_test.cpp units_test.cpp)
target_link_libraries(meta_tests PRIVATE meta GTest::gtest_main)
add_test(NAME meta_tests COMMAND meta_tests)
