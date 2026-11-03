cmake_minimum_required(VERSION 3.21)
project(BillingUntested CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(billing
    src/tariff.cpp
    src/meter.cpp
)
target_include_directories(billing PUBLIC include)

enable_testing()
add_subdirectory(tests)
