find_package(GTest REQUIRED)

add_executable(calc_tests
    strutil_test.cpp
    rational_test.cpp
    lexer_test.cpp
    expr_test.cpp
    parser_test.cpp
    calc_test.cpp
)
target_link_libraries(calc_tests PRIVATE calc GTest::gtest_main)
add_test(NAME calc_tests COMMAND calc_tests)

# The command-line program, end to end.
add_test(NAME tinycalc_args COMMAND tinycalc "1 + 2 * 3" "ans / 4" "-(ans - 1)")
set_tests_properties(tinycalc_args PROPERTIES PASS_REGULAR_EXPRESSION "^7\r?\n7/4\r?\n-3/4\r?\n$")
add_test(NAME tinycalc_rejects_bad_input COMMAND tinycalc "1 +")
set_tests_properties(tinycalc_rejects_bad_input PROPERTIES WILL_FAIL TRUE)
