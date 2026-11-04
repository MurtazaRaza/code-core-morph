find_package(GTest REQUIRED)

add_executable(textkit_tests
    ring_test.cpp
    report_test.cpp
)
target_link_libraries(textkit_tests PRIVATE textkit GTest::gtest_main)
add_test(NAME textkit_tests COMMAND textkit_tests)
