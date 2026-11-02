cmake_minimum_required(VERSION 3.21)
project(TextPipeline CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_subdirectory(src/util)
add_subdirectory(src/core)
add_subdirectory(app)

enable_testing()
add_subdirectory(tests)
