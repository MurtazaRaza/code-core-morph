find_package(GTest REQUIRED)

add_executable(util_tests strings_test.cpp tokenizer_test.cpp)
target_link_libraries(util_tests PRIVATE mm_util GTest::gtest_main)
add_test(NAME util_tests COMMAND util_tests)

add_executable(core_tests document_test.cpp counter_test.cpp pipeline_test.cpp)
target_link_libraries(core_tests PRIVATE mm_core GTest::gtest_main)
add_test(NAME core_tests COMMAND core_tests)
