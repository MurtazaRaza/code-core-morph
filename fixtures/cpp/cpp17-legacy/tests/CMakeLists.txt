find_package(GTest REQUIRED)

add_executable(pkgmeta_tests
    version_test.cpp
    manifest_test.cpp
    render_test.cpp
    resolve_test.cpp
)
target_link_libraries(pkgmeta_tests PRIVATE pkgmeta GTest::gtest_main)
add_test(NAME pkgmeta_tests COMMAND pkgmeta_tests)
