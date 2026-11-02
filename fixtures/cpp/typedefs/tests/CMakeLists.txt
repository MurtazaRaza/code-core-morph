find_package(GTest REQUIRED)

add_executable(geo_tests
    vector_test.cpp
    shapes_test.cpp
    registry_test.cpp
    stats_test.cpp
    grid_test.cpp
)
target_link_libraries(geo_tests PRIVATE geo GTest::gtest_main)
add_test(NAME geo_tests COMMAND geo_tests)
