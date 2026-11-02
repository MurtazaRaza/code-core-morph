cmake_minimum_required(VERSION 3.21)
project(CCompat C CXX)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# The C core: compiled as C, its headers are shared with C++.
add_library(ccore src/buffer.c src/hash.c src/strlist.c)
target_include_directories(ccore PUBLIC include)

# C++ wrappers over the C core.
add_library(ccore_cpp src/buffer_wrapper.cpp src/table.cpp)
target_link_libraries(ccore_cpp PUBLIC ccore)

enable_testing()
add_subdirectory(tests)
