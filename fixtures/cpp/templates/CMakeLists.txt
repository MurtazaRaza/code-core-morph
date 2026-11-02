cmake_minimum_required(VERSION 3.21)
project(MetaTemplates CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(meta src/format.cpp src/units.cpp)
target_include_directories(meta PUBLIC include)

enable_testing()
add_subdirectory(tests)
