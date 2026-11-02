find_package(GTest REQUIRED)

add_executable(ccore_tests c_api_test.cpp wrapper_test.cpp)
target_link_libraries(ccore_tests PRIVATE ccore_cpp GTest::gtest_main)
add_test(NAME ccore_tests COMMAND ccore_tests)
