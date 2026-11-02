cmake_minimum_required(VERSION 3.21)
project(GeoTypedefs CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(geo
    src/vector.cpp
    src/shapes.cpp
    src/registry.cpp
    src/stats.cpp
    src/grid.cpp
)
target_include_directories(geo PUBLIC include)

enable_testing()
add_subdirectory(tests)
