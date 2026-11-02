find_package(GTest REQUIRED)

add_executable(inventory_tests
    item_test.cpp
    warehouse_test.cpp
    order_test.cpp
    factory_test.cpp
    cache_test.cpp
)
target_link_libraries(inventory_tests PRIVATE inventory GTest::gtest_main)
add_test(NAME inventory_tests COMMAND inventory_tests)
