cmake_minimum_required(VERSION 3.21)
project(LegacyToolkit CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(toolkit
    src/logger.cpp
    src/parser.cpp
    src/shuffle.cpp
    src/functional.cpp
    src/transaction.cpp
    src/misc.cpp
)
target_include_directories(toolkit PUBLIC include)

enable_testing()
add_subdirectory(tests)
