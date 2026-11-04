find_package(GTest REQUIRED)

add_executable(notes_tests
    note_test.cpp
    store_test.cpp
    format_test.cpp
)
target_link_libraries(notes_tests PRIVATE notes GTest::gtest_main)
add_test(NAME notes_tests COMMAND notes_tests)
