find_package(GTest REQUIRED)

add_executable(jobs_tests
    ids_test.cpp
    job_test.cpp
    queue_test.cpp
    events_test.cpp
    document_test.cpp
    worker_test.cpp
)
target_link_libraries(jobs_tests PRIVATE jobs GTest::gtest_main)
add_test(NAME jobs_tests COMMAND jobs_tests)

# The command-line program, end to end.
add_test(NAME jobrun_order COMMAND jobrun "low:1" "high:5" "mid:3" "mid2:3")
set_tests_properties(jobrun_order PROPERTIES PASS_REGULAR_EXPRESSION
    "^#2 high \\(priority 5\\)\r?\n#3 mid \\(priority 3\\)\r?\n#4 mid2 \\(priority 3\\)\r?\n#1 low \\(priority 1\\)\r?\n4 jobs\r?\n$")
