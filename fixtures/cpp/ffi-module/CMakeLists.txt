cmake_minimum_required(VERSION 3.21)
project(Textkit CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# The ring buffer and the checksum sit behind a C API (include/textkit/ring.h); the report is
# C++ that uses it.
add_library(textkit
    src/ring.cpp
    src/report.cpp
)
target_include_directories(textkit PUBLIC include)

enable_testing()
add_subdirectory(tests)
